"""
Training data generator for video search modality weight distillation.

Uses Amazon Nova Premier as the teacher model to generate modality weight
labels (visual, audio, transcription, metadata) for video search queries.
"""

import json
import boto3
import random
from typing import Dict, List
import time

# Initialize Bedrock Runtime client
bedrock_runtime = boto3.client("bedrock-runtime", region_name="us-east-1")

# Model configuration
NOVA_PREMIER_MODEL_ID = "us.amazon.nova-premier-v1:0"

# Teacher system prompt — detailed, for generating labels with Nova Premier
TEACHER_SYSTEM_PROMPT = """Analyze video search queries and assign weights (0.0-1.0) for four modalities.
Weights must sum to 1.0.

Return ONLY valid JSON in this exact format:
{
  "visual": 0.0,
  "audio": 0.0,
  "transcription": 0.0,
  "metadata": 0.0,
  "reasoning": "brief explanation"
}

Guidelines:
- visual: For appearance, colors, objects, actions, scenes, people's looks
- audio: For sounds, music, noise, non-speech audio
- transcription: For spoken words, dialogue, narration, text content
- metadata: For searching by person name, genre, captions, keywords, factual attributes

Examples:
- "red car driving" → visual=0.9, metadata=0.1
- "person saying hello" → transcription=0.5, visual=0.2, audio=0.2, metadata=0.1
- "Cristiano Ronaldo" → metadata=0.6, visual=0.3, transcription=0.1
- "dog barking loudly" → audio=0.6, visual=0.3, metadata=0.1"""

# Student system prompt — minimal, baked into distillation training data
STUDENT_SYSTEM_PROMPT = """Return JSON with visual, audio, transcription, metadata weights (sum=1.0) and reasoning for the given video search query."""

# Weight keys for validation
WEIGHT_KEYS = ["visual", "audio", "transcription", "metadata"]

# ---------------------------------------------------------------------------
# Query templates — designed to produce natural, semantic video search queries
# ---------------------------------------------------------------------------

QUERY_TEMPLATES = {
    "visual_dominant": [
        # Person + action + context
        "{name} riding a {vehicle}",
        "{name} walking through {place}",
        "{name} standing next to a {object}",
        "{name} driving {seeing_detail}",
        "{person} taking a phone call next to a {object}",
        "{person} sitting on a {furniture} in {place}",
        # Scene descriptions
        "{color} {object} on the {surface}",
        "portrait of {count} {people_word} hanging on the wall",
        "dark {place_type} in {show_title}",
        "aerial shot of {landscape}",
        "close-up of {food} on a plate",
        "timelapse of {city} at {time_of_day}",
        "{animal} running across {terrain}",
        "{weather} sky over {landscape}",
        # Title / UI elements
        "{show_title} title page appears",
        "{show_title} opening credits",
        "{show_title} logo on screen",
        # Sports visuals
        "goal keeper saving a goal",
        "long pass in {sport}",
        "{player_action} in {sport}",
        "slow motion replay of {sport_moment}",
        # Everyday scenes
        "crowd walking through {city} streets",
        "{person} looking out the window of a {vehicle}",
        "children playing in {place}",
        "{person} holding a {handheld_object}",
        # Complex multi-detail visual queries
        "{name} wearing a {color} jacket walking through {city} at {time_of_day}",
        "split screen showing {landscape} on the left and {city} skyline on the right",
        "{animal} and its young crossing {terrain} during a {weather} day",
    ],
    "audio_dominant": [
        # Environmental sounds
        "{weather_sound} sound in the background",
        "{ambient_sound} at {time_of_day}",
        "loud {impact_sound} echoing through {place_type}",
        # Animal sounds
        "{animal} {animal_sound} in the distance",
        "birds singing in the {nature_place}",
        # Music
        "{instrument} solo during {scene_type}",
        "{music_style} music playing over {scene_type}",
        "background {music_style} music",
        "{instrument} playing softly",
        # Mechanical / urban
        "engine {engine_sound} of a {vehicle}",
        "construction noise near {place}",
        "sirens going off in the {city} streets",
        # Crowd / event sounds
        "crowd {crowd_sound} in the stadium",
        "audience laughing during {show_title}",
        "fireworks going off at {event}",
        "glass breaking sound effect",
        "gunshot sound in {genre} movie",
        # Complex layered audio
        "{instrument} and {instrument} duet with {ambient_sound} in the background",
        "sudden {impact_sound} followed by {animal} {animal_sound} in a {weather} night",
    ],
    "transcription_dominant": [
        # Discussions / interviews
        "{name} discuss about the {topic_phrase}",
        "{name} discuss the importance of {subject} to {broad_context}",
        "{name} explaining how {concept} works",
        "{name} talking about {personal_topic}",
        "interview with {name} about {topic_phrase}",
        "{person_role} describing the {process}",
        # Presentations / lectures
        "presentation on {topic_phrase}",
        "lecture about {academic_subject}",
        "tutorial on how to {skill_action}",
        "keynote speech about {topic_phrase}",
        # Conversations
        "conversation between {name} and {name2} about {topic_phrase}",
        "{person_role} answering questions about {subject}",
        "panel discussion on {topic_phrase}",
        # Narration
        "{name} narrating a documentary about {subject}",
        "voiceover explaining the {process}",
        "{person_role} reading {written_content} aloud",
        # Complex multi-topic speech
        "{name} comparing {subject} and {concept} in a {academic_subject} lecture",
        "heated debate between {name} and {name2} about the {topic_phrase} at {event}",
    ],
    "metadata_dominant": [
        # Person names (pure identity search)
        "{famous_person}",
        "{famous_person} highlights",
        "{famous_person} best moments",
        # Sports + person
        "{famous_athlete} score a {sport_score}",
        "{famous_athlete} {sport_play} in {sport_event}",
        "{sports_league} {sport_event_type}",
        # Show / content titles
        "{show_title} season {season_number}",
        "{show_title} episode {season_number}",
        "{show_title} behind the scenes",
        # Genre + content type
        "{genre} {content_type} from {year}",
        "{year} {genre} {content_type}",
        "best {genre} {content_type} of {year}",
        # Network / platform
        "{network} {show_type}",
        "{network} original {content_type}",
        # Tagged / keyword searches
        "videos about {keyword_topic}",
        "{keyword_topic} explained",
        # Complex metadata queries
        "{famous_person} guest appearance on {show_title} season {season_number}",
        "{famous_athlete} vs {famous_athlete} in {sports_league} {sport_event_type}",
    ],
    "balanced": [
        # Person + speech + action
        "{name} {speaking_action} while {physical_action}",
        "{name} {speaking_action} in front of {audience}",
        "{person} {emotional_reaction} after hearing the {news_type}",
        # Scene + sound
        "{weather} scene with {ambient_sound} in the background",
        "{animal} {physical_action} and {animal_sound}",
        "people {group_activity} in {place} with {music_style} music",
        # Event + multiple modalities
        "{crowd_action} at the {event_type} while {name} performs",
        "audience reacting to {name} at {event_type}",
        "{person_role} demonstrating {skill_action} on stage",
        "{name} singing while walking through {city}",
        # Complex scenes
        "{person} whispering to {person} in a {place_type}",
        "protesters chanting in front of {landmark}",
        "{name} being interviewed on the {surface} of {place}",
        # Complex multi-modality queries
        "{name} explaining {concept} to {audience} while {music_style} music plays in a {weather} setting",
        "{famous_person} giving a keynote at {event} with crowd {crowd_sound} and {instrument} playing",
    ],
}

# ---------------------------------------------------------------------------
# Vocabulary — rich, natural terms for filling templates
# ---------------------------------------------------------------------------

VOCABULARY = {
    # First names (casual, like real search queries)
    "name": [
        "Werner", "Kevin", "Scott", "Sarah", "James", "Maria", "Chen",
        "Ahmed", "Lisa", "David", "Emma", "Carlos", "Priya", "Michael",
        "Sophie", "Daniel", "Yuki", "Aisha", "Robert", "Elena",
        "Marcus", "Olivia", "Raj", "Hannah", "Alex", "Nina", "Tom",
        "Grace", "Leo", "Fatima",
    ],
    "name2": [
        "Sarah", "James", "Maria", "the host", "the interviewer",
        "a journalist", "the moderator", "a guest expert",
    ],
    # Generic person descriptors
    "person": [
        "a man", "a woman", "a child", "a teenager", "an elderly man",
        "a young woman", "a boy", "a girl", "someone", "a stranger",
    ],
    "person_role": [
        "the CEO", "the doctor", "the professor", "the chef", "the engineer",
        "the journalist", "the scientist", "the pilot", "the coach",
        "the guide", "the narrator", "the host",
    ],
    "people_word": ["men", "women", "people", "soldiers", "musicians"],

    # Famous people (for metadata-dominant)
    "famous_person": [
        "Cristiano Ronaldo", "Taylor Swift", "Elon Musk", "Beyonce",
        "LeBron James", "Oprah Winfrey", "David Attenborough",
        "Serena Williams", "Barack Obama", "Adele", "Tom Hanks",
        "Lionel Messi", "Rihanna", "Dwayne Johnson", "Emma Watson",
        "Gordon Ramsay", "Billie Eilish", "Keanu Reeves",
        "Lewis Hamilton", "Simone Biles", "Roger Federer",
        "Jeff Bezos", "Mark Zuckerberg", "Tim Cook",
    ],
    "famous_athlete": [
        "LeBron James", "Cristiano Ronaldo", "Lionel Messi",
        "Serena Williams", "Tom Brady", "Neymar", "Stephen Curry",
        "Patrick Mahomes", "Kylian Mbappe", "Simone Biles",
        "Lewis Hamilton", "Roger Federer", "Usain Bolt",
    ],

    # Places
    "place": [
        "the market", "a coffee shop", "the office", "the beach",
        "a park", "the factory", "a hospital", "a classroom",
        "the airport", "a library", "downtown", "the rooftop",
        "the warehouse", "a garden", "the lab", "a village",
    ],
    "place_type": [
        "cave", "tunnel", "hallway", "room", "alley", "basement",
        "warehouse", "corridor", "forest", "church", "temple",
    ],
    "city": [
        "Porto", "Tokyo", "New York", "London", "São Paulo",
        "Mumbai", "Berlin", "Dubai", "Sydney", "Paris", "Lagos",
        "Seoul", "Istanbul", "Mexico City", "Singapore",
    ],
    "landscape": [
        "the mountains", "the coastline", "the desert", "a river valley",
        "the city skyline", "the countryside", "a frozen lake",
        "the rainforest canopy", "a volcanic island",
    ],
    "nature_place": [
        "forest", "garden", "jungle", "meadow", "wetlands",
    ],
    "landmark": [
        "the capitol building", "the courthouse", "the stadium",
        "the factory gates", "city hall", "the embassy",
    ],
    "terrain": [
        "the field", "the sand", "a snowy hill", "the road",
        "a muddy trail", "the ice", "the grass",
    ],
    "surface": [
        "steps", "balcony", "rooftop", "sideline", "stage",
    ],

    # Objects
    "object": [
        "vintage car", "red bus", "bicycle", "motorcycle",
        "old typewriter", "telescope", "guitar", "suitcase",
        "painting", "statue", "boat", "camera", "lantern",
        "umbrella", "backpack", "trophy", "flag", "microphone",
    ],
    "handheld_object": [
        "book", "phone", "coffee cup", "map", "umbrella", "camera",
        "flashlight", "tablet", "microphone", "flower", "flag",
    ],
    "furniture": [
        "bench", "chair", "couch", "stool", "swing", "hammock",
    ],
    "food": [
        "sushi", "pasta", "steak", "fresh bread", "fruit bowl",
        "birthday cake", "curry", "tacos",
    ],

    # Vehicles
    "vehicle": [
        "motorcycle", "bicycle", "bus", "train", "helicopter",
        "vintage car", "sports car", "truck", "boat", "canoe",
    ],

    # Animals
    "animal": [
        "dog", "cat", "horse", "eagle", "whale", "elephant",
        "wolf", "deer", "bear", "dolphin", "owl", "fox",
    ],
    "animal_sound": [
        "barking", "howling", "growling", "chirping", "roaring",
        "meowing", "squeaking", "singing",
    ],

    # Colors
    "color": [
        "red", "blue", "golden", "white", "black", "silver",
        "green", "yellow", "orange", "rusty",
    ],

    # Counts
    "count": ["two", "three", "four", "several"],

    # Weather / time
    "weather": [
        "rainy", "foggy", "snowy", "sunny", "overcast", "stormy",
    ],
    "weather_sound": [
        "thunder storm", "heavy rain", "wind howling", "hail storm",
        "ocean waves crashing", "rain and wind",
    ],
    "time_of_day": [
        "dawn", "sunrise", "midday", "sunset", "dusk", "night",
        "golden hour", "midnight",
    ],

    # Sounds
    "ambient_sound": [
        "crickets chirping", "rain falling", "wind blowing",
        "distant traffic", "waves lapping", "birds singing",
        "city noise", "river flowing",
    ],
    "impact_sound": [
        "crash", "bang", "thud", "explosion", "clang", "boom",
    ],
    "crowd_sound": [
        "cheering", "roaring", "chanting", "clapping", "booing",
    ],
    "engine_sound": [
        "roaring", "revving", "humming", "sputtering", "idling",
    ],

    # Music
    "instrument": [
        "guitar", "piano", "violin", "drums", "saxophone",
        "cello", "trumpet", "flute", "harmonica",
    ],
    "music_style": [
        "jazz", "classical", "ambient", "rock", "electronic",
        "folk", "orchestral", "cinematic", "acoustic",
    ],

    # Actions
    "physical_action": [
        "walking", "running", "sitting down", "pacing",
        "gesturing", "pointing at something", "looking around",
        "climbing", "riding", "dancing",
    ],
    "speaking_action": [
        "discussing", "explaining", "talking about", "describing",
        "presenting", "arguing about", "debating",
    ],
    "emotional_reaction": [
        "celebrating", "crying", "laughing", "gasping",
        "looking shocked", "smiling", "getting emotional",
    ],
    "group_activity": [
        "dancing", "eating", "working", "protesting",
        "celebrating", "exercising", "marching",
    ],
    "crowd_action": [
        "crowd cheering", "fans singing", "audience clapping",
        "people dancing", "crowd chanting",
    ],
    "player_action": [
        "slam dunk", "penalty kick", "header goal",
        "three pointer", "touchdown pass", "home run",
        "diving catch", "free kick", "fast break",
    ],

    # Visual details
    "seeing_detail": [
        "seeing a woman in the rearview mirror",
        "through the rain",
        "past the old buildings",
        "along the coast",
        "into the sunset",
        "through a tunnel",
        "across the bridge",
    ],

    # Sports
    "sport": [
        "american football", "basketball", "soccer", "tennis",
        "baseball", "cricket", "rugby", "hockey", "volleyball",
    ],
    "sport_moment": [
        "the winning goal", "the final play", "the knockout punch",
        "the match point", "the buzzer beater", "a spectacular save",
    ],
    "sport_score": [
        "basket", "goal", "touchdown", "home run", "try", "point",
    ],
    "sport_play": [
        "scoring", "dribbling past defenders", "making a save",
        "hitting a winner", "blocking a shot", "stealing the ball",
    ],
    "sport_event": [
        "the finals", "the championship game", "a friendly match",
        "the World Cup", "the playoffs", "the Olympics",
    ],
    "sport_event_type": [
        "finals highlights", "championship", "playoffs",
        "match recap", "draft picks", "best goals of the season",
    ],
    "sports_league": [
        "NFL", "NBA", "Premier League", "FIFA World Cup",
        "Olympics", "UFC", "Formula 1", "MLB", "Champions League",
    ],

    # Topics / subjects (natural phrasing)
    "topic_phrase": [
        "innovation in the city of Porto",
        "future of electric vehicles",
        "impact of social media on youth",
        "rise of artificial intelligence",
        "challenges facing small businesses",
        "climate change and coastal cities",
        "history of space exploration",
        "digital transformation in healthcare",
        "state of the global economy",
        "future of remote work",
        "evolution of street food culture",
        "role of women in technology",
        "ethics of gene editing",
        "mental health awareness",
        "renewable energy breakthroughs",
    ],
    "subject": [
        "rice to human civilization", "water conservation",
        "blockchain technology", "quantum computing",
        "sustainable farming", "ancient architecture",
        "machine learning", "ocean ecosystems",
        "renewable energy", "urban planning",
        "deep sea exploration", "vaccine development",
    ],
    "broad_context": [
        "human civilization", "modern society", "the global economy",
        "future generations", "developing countries",
        "the tech industry", "everyday life",
    ],
    "concept": [
        "neural networks", "supply chain logistics",
        "photosynthesis", "black holes",
        "cryptocurrency mining", "gene therapy",
        "cloud computing", "fermentation",
    ],
    "personal_topic": [
        "his childhood", "her career journey", "growing up in poverty",
        "overcoming failure", "life after retirement",
        "moving to a new country", "starting a business",
    ],
    "process": [
        "manufacturing process", "brewing technique",
        "surgical procedure", "training regimen",
        "design workflow", "cooking method",
        "restoration process", "migration pattern",
    ],
    "academic_subject": [
        "molecular biology", "ancient civilizations", "calculus",
        "existential philosophy", "behavioral economics",
        "astrophysics", "art history", "marine biology",
    ],
    "skill_action": [
        "cook a perfect steak", "edit videos professionally",
        "build a website", "play the guitar",
        "paint with watercolors", "do CPR",
        "change a tire", "brew coffee properly",
    ],
    "written_content": [
        "a poem", "the news bulletin", "a letter",
        "the research findings", "an excerpt from the book",
    ],
    "news_type": [
        "election results", "announcement", "verdict",
        "breaking news", "diagnosis", "final score",
    ],
    "audience": [
        "a live audience", "the camera", "a crowd of students",
        "the board of directors", "a packed stadium",
        "journalists at a press conference",
    ],

    # Shows / titles / content
    "show_title": [
        "Meridian", "Now Go Build", "The Grand Tour", "Planet Earth",
        "Breaking Bad", "Stranger Things", "The Crown",
        "Black Mirror", "Chef's Table", "Our Planet",
        "Abstract", "Formula 1 Drive to Survive", "Wild Isles",
    ],
    "genre": [
        "comedy", "drama", "horror", "documentary", "action",
        "thriller", "romance", "sci-fi", "true crime", "adventure",
    ],
    "content_type": [
        "movie", "series", "film", "documentary", "short film",
        "mini-series", "special",
    ],
    "show_type": [
        "original series", "documentary series", "news special",
        "reality show", "talk show", "sports coverage",
    ],
    "network": [
        "Netflix", "HBO", "BBC", "Amazon Prime", "Disney+",
        "ESPN", "National Geographic", "Apple TV",
    ],
    "year": ["2020", "2021", "2022", "2023", "2024", "2025"],
    "season_number": ["1", "2", "3", "4", "5"],
    "keyword_topic": [
        "sustainable energy", "artificial intelligence",
        "street food culture", "space tourism",
        "electric vehicles", "mental health",
        "cryptocurrency", "ocean conservation",
    ],

    # Events
    "event_type": [
        "concert", "award ceremony", "sports game", "rally",
        "festival", "conference", "comedy show", "graduation",
    ],
    "event": [
        "New Year's Eve", "the World Cup final", "the concert",
        "the ceremony", "the festival",
    ],

    # Scene types
    "scene_type": [
        "a chase scene", "the opening scene", "a romantic scene",
        "a training montage", "the climax", "a flashback",
        "a cooking scene", "a fight scene",
    ],
}


def generate_query_from_template(template: str, vocab: Dict) -> str:
    """Generate a query by filling template placeholders with random vocabulary."""
    query = template
    while "{" in query:
        start = query.index("{")
        end = query.index("}")
        placeholder = query[start + 1 : end]

        if placeholder in vocab:
            replacement = random.choice(vocab[placeholder])
            query = query[:start] + replacement + query[end + 1 :]
        else:
            query = query[:start] + query[end + 1 :]

    return query


def generate_diverse_queries(num_queries: int) -> List[str]:
    """Generate diverse video search queries across all modality categories."""
    queries = []

    # Distribution: visual 25%, audio 20%, transcription 20%, metadata 15%, balanced 20%
    visual_count = int(num_queries * 0.25)
    audio_count = int(num_queries * 0.20)
    transcription_count = int(num_queries * 0.20)
    metadata_count = int(num_queries * 0.15)
    balanced_count = num_queries - visual_count - audio_count - transcription_count - metadata_count

    for category, count in [
        ("visual_dominant", visual_count),
        ("audio_dominant", audio_count),
        ("transcription_dominant", transcription_count),
        ("metadata_dominant", metadata_count),
        ("balanced", balanced_count),
    ]:
        templates = QUERY_TEMPLATES[category]
        for _ in range(count):
            template = random.choice(templates)
            query = generate_query_from_template(template, VOCABULARY)
            queries.append(query)

    random.shuffle(queries)
    return queries


def get_teacher_response(query: str, max_retries: int = 3) -> Dict:
    """Get modality weight prediction from Nova Premier teacher model."""
    for attempt in range(max_retries):
        try:
            response = bedrock_runtime.converse(
                modelId=NOVA_PREMIER_MODEL_ID,
                messages=[{"role": "user", "content": [{"text": query}]}],
                system=[{"text": TEACHER_SYSTEM_PROMPT}],
                inferenceConfig={"maxTokens": 200, "temperature": 0.3},
            )

            content = response["output"]["message"]["content"][0]["text"]

            # Extract JSON from response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                weights_data = json.loads(content[start:end])

                # Validate all four weights are present and sum to 1.0
                total = sum(weights_data.get(k, 0) for k in WEIGHT_KEYS)
                if abs(total - 1.0) > 0.01:
                    raise ValueError(f"Weights sum to {total}, not 1.0")

                for key in WEIGHT_KEYS:
                    if key not in weights_data:
                        raise ValueError(f"Missing weight key: {key}")

                return weights_data
            else:
                raise ValueError("No JSON found in response")

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Retry {attempt + 1}/{max_retries}: {e}")
                time.sleep(1)
            else:
                print(f"  Failed after {max_retries} attempts: {e}")
                return None

    return None


def generate_training_dataset(
    num_samples: int = 10000,
    output_file: str = "training_data.jsonl",
    checkpoint_file: str = "checkpoint.jsonl",
    batch_size: int = 100,
):
    """Generate training dataset with checkpointing."""

    print(f"Generating {num_samples} training examples...")
    print(f"Output file: {output_file}")
    print(f"Checkpoint file: {checkpoint_file}")
    print()

    # Load checkpoint if exists
    completed = []
    try:
        with open(checkpoint_file, "r") as f:
            completed = [json.loads(line) for line in f]
        print(f"Resuming from checkpoint: {len(completed)} examples already completed")
    except FileNotFoundError:
        print("Starting fresh generation")

    start_idx = len(completed)

    # Generate queries
    queries = generate_diverse_queries(num_samples)

    # Process remaining queries
    for i in range(start_idx, num_samples):
        query = queries[i]

        if (i + 1) % 10 == 0:
            print(f"Processing {i + 1}/{num_samples} - Query: '{query}'")

        response = get_teacher_response(query)

        if response is None:
            print(f"  Skipping query due to errors")
            continue

        training_example = {
            "schemaVersion": "bedrock-conversation-2024",
            "system": [{"text": STUDENT_SYSTEM_PROMPT}],
            "messages": [
                {"role": "user", "content": [{"text": query}]},
                {"role": "assistant", "content": [{"text": json.dumps(response)}]},
            ],
        }

        completed.append(training_example)

        # Write to checkpoint every batch_size examples
        if len(completed) % batch_size == 0:
            with open(checkpoint_file, "w") as f:
                for example in completed:
                    f.write(json.dumps(example) + "\n")
            print(f"  Checkpoint saved: {len(completed)} examples")

        # Rate limiting
        time.sleep(0.5)

    # Write final output
    with open(output_file, "w") as f:
        for example in completed:
            f.write(json.dumps(example) + "\n")

    print(f"\nGeneration complete!")
    print(f"  Total examples: {len(completed)}")
    print(f"  Output file: {output_file}")


def generate_eval_dataset(
    num_samples: int = 100,
    output_file: str = "eval_dataset_v2.jsonl",
):
    """Generate a holdout evaluation dataset with balanced modality distribution.

    Output format: {"query": "...", "reference": "{\"visual\": ..., ...}"}
    Ground truth contains only numeric weights (no reasoning) for clean evaluation.
    """
    print(f"Generating {num_samples} evaluation examples...")
    print(f"Output file: {output_file}")
    print()

    # Balanced distribution for eval: 30 visual, 20 audio, 20 transcription, 20 metadata, 10 balanced
    category_counts = {
        "visual_dominant": int(num_samples * 0.30),
        "audio_dominant": int(num_samples * 0.20),
        "transcription_dominant": int(num_samples * 0.20),
        "metadata_dominant": int(num_samples * 0.20),
    }
    category_counts["balanced"] = num_samples - sum(category_counts.values())

    queries_by_category = {}
    for category, count in category_counts.items():
        templates = QUERY_TEMPLATES[category]
        cat_queries = []
        for _ in range(count):
            template = random.choice(templates)
            cat_queries.append(generate_query_from_template(template, VOCABULARY))
        queries_by_category[category] = cat_queries

    # Flatten and shuffle
    all_queries = []
    for cat, qs in queries_by_category.items():
        for q in qs:
            all_queries.append((cat, q))
    random.shuffle(all_queries)

    completed = []
    for i, (cat, query) in enumerate(all_queries):
        if (i + 1) % 10 == 0:
            print(f"Processing {i + 1}/{num_samples} - Query: '{query}'")

        response = get_teacher_response(query)
        if response is None:
            print(f"  Skipping query due to errors")
            continue

        # Strip reasoning for clean eval ground truth
        ref_weights = {k: response[k] for k in WEIGHT_KEYS}
        completed.append({
            "query": query,
            "reference": json.dumps(ref_weights),
        })

        time.sleep(0.5)

    with open(output_file, "w") as f:
        for example in completed:
            f.write(json.dumps(example) + "\n")

    # Show distribution
    dom_counts = {}
    for ex in completed:
        ref = json.loads(ex["reference"])
        dom = max(WEIGHT_KEYS, key=lambda k: float(ref[k]))
        dom_counts[dom] = dom_counts.get(dom, 0) + 1

    print(f"\nEval generation complete!")
    print(f"  Total examples: {len(completed)}")
    print(f"  Distribution by dominant modality:")
    for k in WEIGHT_KEYS:
        print(f"    {k:15s}: {dom_counts.get(k, 0)}")
    print(f"  Output file: {output_file}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate training/eval data for modality weight distillation")
    parser.add_argument("--mode", choices=["train", "eval", "both", "test"], default="both",
                        help="train: training data only, eval: eval data only, both: both, test: quick 10-sample test")
    parser.add_argument("--train-samples", type=int, default=10000, help="Number of training samples")
    parser.add_argument("--eval-samples", type=int, default=100, help="Number of eval samples")
    parser.add_argument("--train-output", default="distillation_dataset_v2.jsonl", help="Training output file")
    parser.add_argument("--eval-output", default="eval_dataset_v2.jsonl", help="Eval output file")
    args = parser.parse_args()

    if args.mode == "test":
        print("=" * 70)
        print("QUICK TEST: 10 samples with Nova Premier")
        print("=" * 70)
        print()
        queries = generate_diverse_queries(10)
        for i, query in enumerate(queries):
            print(f"[{i+1}/10] Query: \"{query}\"")
            response = get_teacher_response(query)
            if response:
                w = " | ".join(f"{k}={response[k]}" for k in WEIGHT_KEYS)
                print(f"  {w}")
                print(f"  reasoning: {response['reasoning']}")
            else:
                print("  FAILED")
            print()

    if args.mode in ("train", "both"):
        print("=" * 70)
        print(f"GENERATING TRAINING DATA ({args.train_samples} samples)")
        print("=" * 70)
        print()
        generate_training_dataset(
            num_samples=args.train_samples,
            output_file=args.train_output,
            checkpoint_file=args.train_output.replace(".jsonl", "_checkpoint.jsonl"),
        )
        print()

    if args.mode in ("eval", "both"):
        print("=" * 70)
        print(f"GENERATING EVAL DATA ({args.eval_samples} samples)")
        print("=" * 70)
        print()
        generate_eval_dataset(
            num_samples=args.eval_samples,
            output_file=args.eval_output,
        )
