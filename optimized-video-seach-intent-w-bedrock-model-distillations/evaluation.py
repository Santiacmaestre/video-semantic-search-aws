"""Helper functions for Bedrock model evaluation and benchmarking."""

import json
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

WEIGHT_KEYS = ["visual", "audio", "transcription", "metadata"]


def get_model_response(bedrock_runtime, model_id, query, system_msg):
    """Call a model via Bedrock Converse API and return parsed weights + token count."""
    response = bedrock_runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": query}]}],
        system=[{"text": system_msg}],
        inferenceConfig={"maxTokens": 200, "temperature": 0.3},
    )
    text = response["output"]["message"]["content"][0]["text"]
    tokens = response["usage"]["totalTokens"]

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        parsed = json.loads(text[start:end])
        return parsed, tokens
    return {"raw": text}, tokens


def write_eval_dataset(eval_samples, system_prompt, output_path):
    """Write a Bedrock evaluation dataset JSONL file for a specific model's prompt."""
    with open(output_path, "w") as f:
        for sample in eval_samples:
            eval_prompt = f"{system_prompt}\n\nQuery: {sample['query']}"
            f.write(json.dumps({
                "prompt": eval_prompt,
                "referenceResponse": sample["reference"],
            }) + "\n")
    return output_path


def launch_evaluation_job(
    bedrock_client, job_name, model_id, eval_data_s3_uri,
    role_arn, output_s3_uri, judge_model, dataset_name,
    custom_metrics,
):
    """Create a Bedrock evaluation job with one or more custom metrics.

    Args:
        custom_metrics: List of dicts, each with keys: name, instructions, rating_scale.
    """
    metric_names = [m["name"] for m in custom_metrics]
    metric_definitions = [
        {
            "customMetricDefinition": {
                "name": m["name"],
                "instructions": m["instructions"],
                "ratingScale": m["rating_scale"],
            }
        }
        for m in custom_metrics
    ]

    response = bedrock_client.create_evaluation_job(
        jobName=job_name,
        jobDescription=f"Modality weight eval: {dataset_name}",
        roleArn=role_arn,
        applicationType="ModelEvaluation",
        evaluationConfig={
            "automated": {
                "datasetMetricConfigs": [
                    {
                        "taskType": "General",
                        "dataset": {
                            "name": dataset_name,
                            "datasetLocation": {"s3Uri": eval_data_s3_uri},
                        },
                        "metricNames": metric_names,
                    }
                ],
                "customMetricConfig": {
                    "customMetrics": metric_definitions,
                    "evaluatorModelConfig": {
                        "bedrockEvaluatorModels": [
                            {"modelIdentifier": judge_model}
                        ]
                    },
                },
            }
        },
        inferenceConfig={
            "models": [
                {
                    "bedrockModel": {
                        "modelIdentifier": model_id,
                        "inferenceParams": json.dumps({
                            "inferenceConfig": {
                                "maxTokens": 200,
                                "temperature": 0.3,
                            }
                        }),
                    }
                }
            ]
        },
        outputDataConfig={"s3Uri": output_s3_uri},
    )
    return response["jobArn"]


def wait_for_eval_jobs(bedrock_client, eval_jobs, poll_interval=60):
    """Poll evaluation jobs until all complete. Returns dict of {label: status}."""
    from datetime import datetime

    pending = dict(eval_jobs)
    final_status = {}

    while pending:
        still_running = {}
        for label, arn in pending.items():
            job_info = bedrock_client.get_evaluation_job(jobIdentifier=arn)
            status = job_info["status"]
            if status in ("Completed", "Failed", "Stopped"):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {label:20s} -> {status}")
                final_status[label] = status
            else:
                still_running[label] = arn

        pending = still_running
        if pending:
            remaining = ", ".join(pending.keys())
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting on: {remaining}")
            time.sleep(poll_interval)

    print("\nAll evaluation jobs complete!")
    return final_status


def bedrock_score_to_rubric(score):
    """Map Bedrock's normalized 0-1 score back to our 1-5 rubric scale.

    Bedrock normalizes all custom metric scores to 0-1 regardless of the
    floatValue settings in the rating scale definition.
    """
    if score is None:
        return None
    return round(score * 4 + 1)  # 0->1, 0.25->2, 0.5->3, 0.75->4, 1.0->5


def load_eval_results(bedrock_client, s3_client, bucket_name, job_arn):
    """Load evaluation output JSONL from S3 for a completed eval job."""
    job_info = bedrock_client.get_evaluation_job(jobIdentifier=job_arn)
    output_uri = job_info["outputDataConfig"]["s3Uri"]
    prefix = output_uri.replace(f"s3://{bucket_name}/", "") + job_info["jobName"]

    output_lines = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".jsonl"):
                print(f"  Found: s3://{bucket_name}/{key}")
                resp = s3_client.get_object(Bucket=bucket_name, Key=key)
                content = resp["Body"].read().decode("utf-8")
                for line in content.strip().split("\n"):
                    if line.strip():
                        output_lines.append(json.loads(line))
    return output_lines


def parse_all_eval_results(bedrock_client, s3_client, bucket_name, eval_jobs, metric_names):
    """Parse evaluation results from all jobs into a scores DataFrame.

    Args:
        metric_names: List of custom metric names to extract scores for.

    Returns (scores_df, summary_by_metric) where summary_by_metric is a dict
    mapping metric_name -> DataFrame with mean/std/median/count per model.
    """
    all_scores = []

    for model_label, job_arn in eval_jobs.items():
        job_info = bedrock_client.get_evaluation_job(jobIdentifier=job_arn)
        print(f"\n{'=' * 60}")
        print(f"Model: {model_label} — Status: {job_info['status']}")
        if job_info.get("failureMessages"):
            print(f"Failures: {job_info['failureMessages']}")
            continue

        try:
            results = load_eval_results(bedrock_client, s3_client, bucket_name, job_arn)
            print(f"  Records: {len(results)}")

            for record in results:
                scores = record.get("automatedEvaluationResult", {}).get("scores", [])

                row = {"model": model_label}
                # Extract each metric score by name
                for metric_name in metric_names:
                    raw_score = None
                    for s in scores:
                        if s.get("metricName") == metric_name:
                            raw_score = s.get("result")
                            break
                    # Fall back to positional if name not found (single-metric jobs)
                    if raw_score is None and len(metric_names) == 1 and scores:
                        raw_score = scores[0].get("result")
                    row[metric_name] = bedrock_score_to_rubric(raw_score)

                all_scores.append(row)

            # Show first 3 examples
            for r in results[:3]:
                prompt = r.get("inputRecord", {}).get("prompt", "")
                query = prompt.split("Query: ")[-1] if "Query: " in prompt else prompt[:60]
                model_resp = r.get("modelResponses", [{}])[0].get("response", "")[:100]
                score_strs = []
                for s in r.get("automatedEvaluationResult", {}).get("scores", []):
                    mapped = bedrock_score_to_rubric(s.get("result"))
                    name = s.get("metricName", "?")
                    score_strs.append(f"{name}={mapped}/5")
                print(f"\n  Query: {query[:60]}")
                print(f"  Pred:  {model_resp}")
                print(f"  Scores: {', '.join(score_strs)}")

        except Exception as e:
            import traceback
            print(f"  Error: {e}")
            traceback.print_exc()

    scores_df = pd.DataFrame(all_scores)

    # Build summary per metric
    summary_by_metric = {}
    for metric_name in metric_names:
        valid = scores_df.dropna(subset=[metric_name])
        summary = valid.groupby("model")[metric_name].agg(["mean", "std", "median", "count"])
        summary_by_metric[metric_name] = summary

    return scores_df, summary_by_metric


def benchmark_latency(bedrock_runtime, eval_models, eval_samples):
    """Time each model's API response on evaluation queries.

    Returns a DataFrame indexed by model with mean_ms, median_ms, p95_ms, std_ms.
    """
    latency_results = []

    for model_label, model_cfg in eval_models.items():
        model_latencies = []
        print(f"Benchmarking {model_label}...", end=" ")

        for sample in eval_samples:
            try:
                start_time = time.perf_counter()
                bedrock_runtime.converse(
                    modelId=model_cfg["id"],
                    messages=[{"role": "user", "content": [{"text": sample["query"]}]}],
                    system=[{"text": model_cfg["prompt"]}],
                    inferenceConfig={"maxTokens": 200, "temperature": 0.3},
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                model_latencies.append(elapsed_ms)
            except Exception as e:
                print(f"\n  Error on query: {e}")

        latency_results.append({
            "model": model_label,
            "mean_ms": np.mean(model_latencies) if model_latencies else 0,
            "median_ms": np.median(model_latencies) if model_latencies else 0,
            "p95_ms": np.percentile(model_latencies, 95) if model_latencies else 0,
            "std_ms": np.std(model_latencies) if model_latencies else 0,
        })
        if model_latencies:
            print(f"mean={np.mean(model_latencies):.0f}ms  "
                  f"median={np.median(model_latencies):.0f}ms  "
                  f"p95={np.percentile(model_latencies, 95):.0f}ms  "
                  f"({len(model_latencies)} calls)")
        else:
            print("no successful calls")

        time.sleep(1)

    latency_df = pd.DataFrame(latency_results).set_index("model")
    return latency_df


def plot_evaluation_results(summary_by_metric, latency_df, model_order, model_colors):
    """Generate a multi-panel chart: one panel per metric + latency."""
    n_metrics = len(summary_by_metric)
    n_panels = n_metrics + 1  # metrics + latency
    fig, axes = plt.subplots(1, n_panels, figsize=(7 * n_panels, 5))
    if n_panels == 1:
        axes = [axes]

    # Determine active models (present in all summaries and latency)
    active_models = list(model_order)
    for summary in summary_by_metric.values():
        active_models = [m for m in active_models if m in summary.index]
    active_models = [m for m in active_models if m in latency_df.index]

    bar_colors_list = [model_colors.get(m, "#999") for m in active_models]
    x = range(len(active_models))

    # --- One panel per metric ---
    for ax, (metric_name, summary) in zip(axes, summary_by_metric.items()):
        scores = [summary.loc[m, "mean"] for m in active_models]
        bars = ax.bar(x, scores, color=bar_colors_list, alpha=0.85, edgecolor="white", linewidth=1.5)
        for bar, val in zip(bars, scores):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                    f"{val:.1f}", ha="center", fontsize=11, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels(active_models, rotation=15)
        ax.set_ylabel("Score (1-5)")
        ax.set_title(metric_name, fontsize=13, fontweight="bold")
        ax.set_ylim(0, 5.5)
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.3, label="Good (4)")
        ax.axhline(y=3, color="orange", linestyle="--", alpha=0.3, label="Acceptable (3)")
        ax.legend(fontsize=9)

    # --- Latency panel ---
    ax_lat = axes[-1]
    latencies = [latency_df.loc[m, "mean_ms"] for m in active_models]
    bars = ax_lat.bar(x, latencies, color=bar_colors_list, alpha=0.85, edgecolor="white", linewidth=1.5)
    for bar, val in zip(bars, latencies):
        ax_lat.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(latencies) * 0.02,
                    f"{val:.0f}ms", ha="center", fontsize=11, fontweight="bold")
    ax_lat.set_xticks(x)
    ax_lat.set_xticklabels(active_models, rotation=15)
    ax_lat.set_ylabel("Latency (ms)")
    ax_lat.set_title("Mean Latency", fontsize=13, fontweight="bold")

    plt.tight_layout()
    plt.savefig("accuracy_vs_latency.png", dpi=150, bbox_inches="tight")
    plt.show()

    # Summary table
    metric_names = list(summary_by_metric.keys())
    header_metrics = "".join(f"{m:>16}" for m in metric_names)
    print("\nModel Comparison")
    print("=" * (20 + 16 * len(metric_names) + 28))
    print(f"{'Model':<20}{header_metrics}{'Mean Latency':>14}{'P95 Latency':>14}")
    print("-" * (20 + 16 * len(metric_names) + 28))
    for model in active_models:
        scores_str = ""
        for mn in metric_names:
            val = summary_by_metric[mn].loc[model, "mean"]
            scores_str += f"{val:>15.1f}"
        lat = latency_df.loc[model, "mean_ms"]
        p95 = latency_df.loc[model, "p95_ms"]
        print(f"{model:<20}{scores_str} {lat:>13.0f}ms {p95:>13.0f}ms")
    print("=" * (20 + 16 * len(metric_names) + 28))
