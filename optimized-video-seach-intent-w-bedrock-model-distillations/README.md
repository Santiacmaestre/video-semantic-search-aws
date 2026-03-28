# Model Distillation for Video Search: Amazon Nova Premier to Nova Micro

This sample demonstrates how to use [Amazon Bedrock Model Distillation](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html) to transfer knowledge from a large teacher model (Amazon Nova Premier) to a small, fast student model (Amazon Nova Micro) for a video search modality weight prediction task.

Given a natural-language video search query, the model predicts four modality weights — **visual**, **audio**, **transcription**, and **metadata** — that sum to 1.0, indicating which content signals are most relevant for retrieving the right video.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Training Pipeline                          │
│                                                                 │
│  generate_training_data.py                                      │
│  ├── Generates synthetic video search queries                   │
│  ├── Labels each with Nova Premier (teacher) using full prompt  │
│  └── Outputs bedrock-conversation-2024 format with short prompt │
│                                                                 │
│  Bedrock Distillation                                           │
│  └── Nova Premier (teacher) ──► Distilled Nova Micro (student)  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      Evaluation Pipeline                        │
│                                                                 │
│  100 holdout queries ──► 2 models compared:                     │
│    ├── Distilled Nova Micro (short student prompt)              │
│    └── Claude Haiku 4.5 (full teacher prompt)                   │
│                                                                 │
│  Bedrock Model Evaluation                                       │
│    └── Custom metric: OverallQuality (1-5, Claude Sonnet judge) │
│                                                                 │
│  Latency benchmark (API timing per model)                       │
└─────────────────────────────────────────────────────────────────┘
```

## Key Design Choice: Dual System Prompts

The teacher model uses a **detailed system prompt** (829 chars) with guidelines and examples to produce high-quality labels. The student is trained with a **minimal one-line prompt** (121 chars) — it learns the task from thousands of examples rather than verbose instructions. This reduces input tokens per call by 85%, lowering cost and latency at inference time.

## Sample Output

```
Query: "Werner discussing innovation in the city of Porto"

{"visual": 0.20, "audio": 0.10, "transcription": 0.45, "metadata": 0.25}
```

## Prerequisites

- An [AWS account](https://aws.amazon.com/free/)
- [Amazon Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) enabled for:
  - Amazon Nova Premier, Nova Micro
  - Anthropic Claude Sonnet 4 (evaluation judge)
  - Anthropic Claude Haiku 4.5 (evaluation candidate)
- An [Amazon SageMaker Studio](https://docs.aws.amazon.com/sagemaker/latest/dg/studio.html) environment or local setup with:
  - Python 3.11+
  - `boto3 >= 1.42.65`, `pandas`, `matplotlib`, `numpy`
- IAM permissions for Bedrock, S3, and IAM role creation

## Repository Structure

```
.
├── video-search-distillation.ipynb   # Main notebook — end-to-end walkthrough
├── generate_training_data.py         # Synthetic query generation + Nova Premier labeling
├── evaluation.py                     # Evaluation helpers: metrics, benchmarking, charts
├── utils.py                          # S3 and IAM helper functions
├── distillation_dataset_v2.jsonl     # 10,000 training samples (bedrock-conversation-2024 format)
├── eval_dataset_v2.jsonl             # 100 holdout evaluation samples
└── README.md
```

| File | Description |
|------|-------------|
| `video-search-distillation.ipynb` | End-to-end notebook: data loading, distillation, deployment, and evaluation with visualizations |
| `generate_training_data.py` | Generates video search queries across 5 categories and labels them with Nova Premier. Outputs `bedrock-conversation-2024` format directly |
| `evaluation.py` | Bedrock evaluation helpers: eval job management, result parsing, latency benchmarking, and visualization |
| `utils.py` | Helper functions for S3 bucket management and IAM role setup |
| `distillation_dataset_v2.jsonl` | Pre-generated training data (10,000 samples) ready for upload to S3 |
| `eval_dataset_v2.jsonl` | 100 holdout samples balanced across modality categories |

## Getting Started

### 1. Clone the repository

```bash
git clone <repository-url>
cd sample-video-semantic-search-multimodal-embeddings/optimized-video-seach-intent-w-bedrock-model-distillations
```

### 2. Run the notebook

Open `video-search-distillation.ipynb` and run cells sequentially. The notebook will:

1. **Load** the pre-generated training data (10,000 samples in Bedrock format)
2. **Upload** to S3 and create the required IAM role
3. **Launch** a Bedrock distillation job (Nova Premier → Nova Micro)
4. **Deploy** the distilled model as an on-demand endpoint
5. **Compare** the distilled model against base Nova Micro on test queries
6. **Evaluate** against Claude Haiku using Bedrock Model Evaluation with a custom metric
7. **Benchmark** latency across all models

### 3. (Optional) Regenerate training data

To regenerate the training and evaluation data from scratch:

```bash
# Generate both training (10,000) and eval (100) datasets (~90 minutes)
python generate_training_data.py --mode both

# Or generate them separately
python generate_training_data.py --mode train --train-samples 10000
python generate_training_data.py --mode eval --eval-samples 100

# Quick test (10 samples, ~30 seconds)
python generate_training_data.py --mode test
```

## How It Works

### Modality Weights

Each query is assigned four weights that sum to 1.0:

| Modality | Description | Example Query |
|----------|-------------|---------------|
| **Visual** | Appearance, colors, objects, actions, scenes | "red car driving on a highway" |
| **Audio** | Sounds, music, noise, non-speech audio | "thunder rumbling in the background" |
| **Transcription** | Spoken words, dialogue, narration | "lecture about marine biology" |
| **Metadata** | Person names, genres, show titles, keywords | "Cristiano Ronaldo highlights" |

### Distillation

[Amazon Bedrock Model Distillation](https://docs.aws.amazon.com/bedrock/latest/userguide/model-distillation.html) transfers the reasoning capabilities of a large teacher model (Nova Premier) into a smaller, faster student model (Nova Micro). The student learns to replicate the teacher's weight assignments while maintaining valid JSON output.

### Evaluation

The distilled model is evaluated against Claude Haiku 4.5 as a baseline:

- **Bedrock Model Evaluation** with a custom OverallQuality metric (1-5 scale) using Claude Sonnet as the LLM judge — scoring both weight accuracy and reasoning quality
- **Latency benchmark** measuring end-to-end API response time

## IAM Permissions

The notebook creates the required IAM roles automatically. Your user/role needs these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:CreateModelCustomizationJob",
        "bedrock:GetModelCustomizationJob",
        "bedrock:CreateCustomModelDeployment",
        "bedrock:GetCustomModelDeployment",
        "bedrock:DeleteCustomModelDeployment",
        "bedrock:CreateEvaluationJob",
        "bedrock:GetEvaluationJob",
        "bedrock:InvokeModel",
        "s3:CreateBucket",
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "iam:CreateRole",
        "iam:CreatePolicy",
        "iam:AttachRolePolicy",
        "iam:UpdateAssumeRolePolicy",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
```

> **Note:** For production use, scope these permissions to specific resource ARNs.

## Cost Considerations

| Resource | Estimated Cost |
|----------|---------------|
| Training data generation | ~10,000 Nova Premier API calls |
| Distillation job | Billed per training token — see [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) |
| On-demand deployment | Billed per inference call to the distilled model |
| Evaluation | ~200 candidate model calls + ~200 Claude Sonnet judge calls |

## Cleanup

The notebook includes a cleanup section (commented out) that deletes:
- The on-demand model deployment
- IAM roles and attached policies
- S3 data

Uncomment and run these cells when done to avoid ongoing charges.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
