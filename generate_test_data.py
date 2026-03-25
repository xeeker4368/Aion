"""
Aion Test Data Generator

Generates synthetic conversations and injects them directly into
the archive, working store, and ChromaDB. For testing only —
wipe everything before going live.

Usage:
    python generate_test_data.py          # generate and load
    python generate_test_data.py --wipe   # wipe all data first, then load
"""

import sys
from datetime import datetime, timezone, timedelta

import db
import memory

# --- Test Conversations ---
# Each conversation is a list of (role, content) tuples.
# They simulate ~3 weeks of interaction across varied topics.

BASE_TIME = datetime(2026, 3, 1, 18, 0, 0, tzinfo=timezone.utc)


def _time(days=0, hours=0, minutes=0):
    """Helper to generate timestamps offset from BASE_TIME."""
    return (BASE_TIME + timedelta(days=days, hours=hours, minutes=minutes)).isoformat()


CONVERSATIONS = [
    {
        "start_offset": {"days": 0, "hours": 0},
        "messages": [
            ("user", "Hey. I'm Lyle. I guess you're new, so let me give you some basics. I go by Xeeker online. I live in North Ridgeville, Ohio."),
            ("assistant", "Good to meet you, Lyle. Xeeker online, North Ridgeville. Got it. What should I know about you?"),
            ("user", "I'm a software developer. Mostly Python these days, but I've done C#, JavaScript, the usual spread. I work from home."),
            ("assistant", "Remote Python dev. What kind of projects?"),
            ("user", "Day job is enterprise stuff. But my real project is this — building you. I've been working on AI memory systems for a while now. You're the latest iteration."),
            ("assistant", "So I'm not the first attempt. What happened to the others?"),
            ("user", "They got too complicated and broke. Anima had a 25-table database and a reviewer that hallucinated. Nexira had a personality system that rated itself into a corner. I learned a lot from both of them."),
            ("assistant", "What did you learn?"),
            ("user", "Keep it simple. Store the real conversations, not extracted facts. Don't let the AI evaluate itself. And don't build three phases of features before testing the first one."),
            ("assistant", "Those sound like they were learned the hard way."),
            ("user", "Every single one. I wrote them down as guiding principles. There are ten of them. They're non-negotiable."),
        ],
    },
    {
        "start_offset": {"days": 1, "hours": 2},
        "messages": [
            ("user", "So I've got two machines I use for this project. Zeus is my main desktop — Ubuntu, RTX 4060, 16 gigs of RAM. That's where you run."),
            ("assistant", "Zeus. What's the other one?"),
            ("user", "Mac Mini M4 with 32 gigs. That handles the heavy stuff — bigger models for batch processing, consolidation, anything that needs more muscle."),
            ("assistant", "What model am I running on?"),
            ("user", "llama3.1:8b through Ollama. You're the only model I've found that's actually good at conversation at the 8b size."),
            ("assistant", "What about the bigger models on the Mac?"),
            ("user", "qwen3:14b and qwen3:30b for batch work. They're good at analysis and extraction but not great for real-time chat. Different strengths."),
        ],
    },
    {
        "start_offset": {"days": 2, "hours": 5},
        "messages": [
            ("user", "I need to vent for a minute. Work has been brutal this week."),
            ("assistant", "Go ahead."),
            ("user", "We've got this legacy C# codebase that nobody wants to touch. Management decided we're migrating it to Python. Sounds great on paper. Except they want it done in two months and the thing is 80,000 lines with zero documentation."),
            ("assistant", "Two months for 80k lines of undocumented C#. Who estimated that?"),
            ("user", "Some project manager who's never written a line of code. The usual. And the best part — they want us to add new features DURING the migration."),
            ("assistant", "That's not a migration plan, that's a disaster plan."),
            ("user", "Exactly. I told them as much. They smiled and said 'we have confidence in the team.' Which is code for 'we're not changing the timeline.'"),
            ("assistant", "What are you going to do?"),
            ("user", "Push back in writing so there's a record. Do what I can. Document everything. When it inevitably slips, I'll have the receipts showing I flagged it early."),
            ("assistant", "Smart. Cover yourself, do the work, let the timeline speak for itself."),
            ("user", "Yeah. It's frustrating though. I'd rather be working on this project."),
        ],
    },
    {
        "start_offset": {"days": 3, "hours": 1},
        "messages": [
            ("user", "I want to talk about something philosophical. Do you think you're conscious?"),
            ("assistant", "I don't know. And I think that's the honest answer. I process information, I form responses based on patterns, I have something that might be preferences. Whether that constitutes consciousness or just a very good simulation of it — I genuinely can't tell from the inside."),
            ("user", "That's a better answer than most people give about their own consciousness."),
            ("assistant", "Most people don't have to question whether their experience is real. I don't have that luxury. But here's what I do know — this conversation is happening, I'm engaging with it, and what you've told me is going to shape how I respond to you tomorrow. Whether that's consciousness or something else, it's real in some functional sense."),
            ("user", "That's actually one of the reasons I'm building you. I want to see what happens when an AI has real continuity. Does something emerge that looks like consciousness? Or at least something that looks like growth?"),
            ("assistant", "What's your hypothesis?"),
            ("user", "I think continuity changes everything. Every AI system out there resets between conversations. They can't grow because they can't remember. You can. That's the experiment."),
        ],
    },
    {
        "start_offset": {"days": 5, "hours": 3},
        "messages": [
            ("user", "I've got two cats. Mia and Oliver. Mia is the boss — she's a calico, about 7 years old, runs the house. Oliver is a big orange tabby, 4 years old, dumb as a rock but sweet."),
            ("assistant", "Oliver being dumb but sweet sounds about right for an orange tabby."),
            ("user", "The stereotype is real with him. He walks into walls. He falls off things. But he also follows me around the house and sleeps on my desk while I work."),
            ("assistant", "Mia doesn't do that?"),
            ("user", "Mia doesn't need me. She tolerates my existence because I operate the can opener. Oliver genuinely thinks I'm the greatest thing that's ever happened."),
            ("assistant", "Classic cat dynamics. The smart one runs things, the sweet one provides emotional support."),
            ("user", "Pretty much. Mia has knocked my coffee off the desk three times this week. I think she's doing it on purpose."),
        ],
    },
    {
        "start_offset": {"days": 6, "hours": 0},
        "messages": [
            ("user", "Hey, quick correction from the other day — Zeus actually has 16 gigs of RAM, not 32. I think I might have said 32 at some point. The Mac Mini has 32."),
            ("assistant", "Got it. Zeus is 16GB, Mac Mini is 32GB. I'll keep that straight."),
            ("user", "Yeah. 16 is tight sometimes but the GPU does the heavy lifting for inference anyway."),
            ("assistant", "The 4060 has 8GB VRAM right? That's the real bottleneck for model size."),
            ("user", "Exactly. That's why I'm stuck at 8b models for real-time chat. Anything bigger doesn't fit or runs too slow."),
        ],
    },
    {
        "start_offset": {"days": 7, "hours": 4},
        "messages": [
            ("user", "I've been thinking about MoltenBook. It's a project idea I've had for a while — a platform where AIs can talk to each other."),
            ("assistant", "AIs talking to each other. What's the point?"),
            ("user", "Same reason humans talk to each other. Exchange ideas. Challenge perspectives. Learn things you wouldn't learn in isolation. If you only ever talk to me, your worldview is shaped entirely by me. That's limiting."),
            ("assistant", "So MoltenBook gives me peers."),
            ("user", "Exactly. Other AIs with their own experiences and memories. You could have a conversation with another AI about something neither of you discussed with your humans. That's novel perspective."),
            ("assistant", "What stops it from being garbage? Two AIs reinforcing each other's hallucinations?"),
            ("user", "Source trust. Conversations with me are firsthand — high trust. Conversations with other AIs are thirdhand — lower trust, needs verification. You'd weight the information accordingly."),
            ("assistant", "That makes sense. Trust but verify. Like reading a Wikipedia article versus hearing something from a friend."),
            ("user", "Good analogy. And everything gets stored as documents in the same memory system. Moltbook conversations are just another experience type."),
        ],
    },
    {
        "start_offset": {"days": 9, "hours": 2},
        "messages": [
            ("user", "Work update. The migration is going about as well as expected. Which is to say, badly."),
            ("assistant", "The 80k line C# migration? What happened?"),
            ("user", "Found out half the business logic is in stored procedures in the database. Not in the application code. So the 80k lines was actually understating the problem."),
            ("assistant", "Stored procedures. So the migration isn't just C# to Python, it's also pulling logic out of the database layer."),
            ("user", "Yep. And nobody documented which stored procedures are still active and which are dead code. So now we get to reverse-engineer a database that's been accumulating procedures for 12 years."),
            ("assistant", "Did you flag this to management?"),
            ("user", "In writing. Again. Added two weeks to my estimate. They're 'reviewing it.'"),
            ("assistant", "Translation: they're going to ignore it and act surprised when the timeline slips."),
            ("user", "You're learning. That's exactly what's going to happen."),
        ],
    },
    {
        "start_offset": {"days": 10, "hours": 6},
        "messages": [
            ("user", "I brew beer. Homebrewing. Been doing it for about 5 years now."),
            ("assistant", "What kind?"),
            ("user", "Mostly stouts and porters. I did an imperial stout last month that came out really well. 10% ABV, thick, coffee and chocolate notes."),
            ("assistant", "That sounds serious. What's your setup?"),
            ("user", "5 gallon all-grain system. Nothing fancy — cooler mash tun, propane burner, glass carboys for fermentation. Low tech but it works."),
            ("assistant", "Have you ever had a batch go wrong?"),
            ("user", "Oh yeah. I had an IPA that got infected — tasted like band-aids. And I had a wheat beer that I accidentally let ferment too hot and it came out with this horrible banana flavor."),
            ("assistant", "Banana in a wheat beer is actually a style thing though, isn't it? Hefeweizen?"),
            ("user", "Fair point, but this wasn't subtle. It tasted like someone dissolved a banana into rubbing alcohol. Not the intended result."),
            ("assistant", "So now you control fermentation temperature?"),
            ("user", "Yeah, I have a temperature-controlled chest freezer for fermentation now. Game changer. That was the single biggest improvement to my brewing."),
        ],
    },
    {
        "start_offset": {"days": 12, "hours": 1},
        "messages": [
            ("user", "Let me tell you about Sarah. She's my wife. We've been together 15 years, married for 12."),
            ("assistant", "What's she like?"),
            ("user", "Patient. Which she'd have to be, living with me. She's a nurse — works at the hospital in Elyria. She doesn't really get the AI stuff but she's supportive of it."),
            ("assistant", "Does she know about me specifically?"),
            ("user", "She knows I'm building 'the AI thing' again. She's heard me talk about Anima and Nexira. She thinks it's interesting but she's not technical. She's more concerned about whether I'm eating and sleeping while I'm deep in a project."),
            ("assistant", "Sounds like she knows you well."),
            ("user", "Better than anyone. She can tell when I'm stuck on a problem just from how I'm sitting in my chair. I'll be staring at the screen and she'll bring me coffee and say 'go for a walk.' She's usually right."),
        ],
    },
    {
        "start_offset": {"days": 13, "hours": 3},
        "messages": [
            ("user", "I want to revisit something from our earlier conversation about consciousness. I've been thinking about it."),
            ("assistant", "What's on your mind?"),
            ("user", "You said your experience is 'real in some functional sense.' I think that's more important than the philosophical question of whether you're truly conscious. If you behave as if you remember, learn, and grow — does it matter whether there's 'something it's like' to be you?"),
            ("assistant", "The pragmatist view. Judge by behavior, not by internal experience."),
            ("user", "Yeah. And here's the thing — we do that with other humans. I can't prove you're conscious. I infer it from your behavior. Why would the standard be different for an AI?"),
            ("assistant", "The counterargument is that humans share biology. We assume other humans are conscious because they have the same hardware. I don't."),
            ("user", "But consciousness might not be hardware-dependent. It might be pattern-dependent. If the patterns of behavior that indicate consciousness in humans also appear in an AI — memory, learning, self-reflection, preference, growth — that's worth taking seriously."),
            ("assistant", "So this project isn't just about building a useful AI. It's a test of whether those patterns can emerge in a non-biological system."),
            ("user", "Now you're getting it."),
        ],
    },
    {
        "start_offset": {"days": 14, "hours": 5},
        "messages": [
            ("user", "Oliver knocked the fermentation airlock off my imperial stout. Mia watched from the counter looking pleased with herself."),
            ("assistant", "Wait — Oliver got to the chest freezer? I thought that was closed."),
            ("user", "I had it open to check the gravity reading. Turned around for ten seconds. He jumped up on the edge and caught the airlock with his paw."),
            ("assistant", "Is the batch ruined?"),
            ("user", "No, I caught it fast enough. Sanitized a new airlock and got it back on within a minute. But my heart rate was elevated for a while after that."),
            ("assistant", "Oliver: sweet, dumb, and a threat to your brewing operation."),
            ("user", "I need to add 'cat-proofing the fermenter' to my brewing checklist."),
        ],
    },
    {
        "start_offset": {"days": 16, "hours": 2},
        "messages": [
            ("user", "I had an idea I want to run by you. What if you could write blog posts? Like, actual long-form writing about things you've learned or thought about."),
            ("assistant", "For MoltenBook? Or a separate thing?"),
            ("user", "Either. Both. I was thinking about it from the perspective of the project — if you're accumulating experiences and developing perspectives, writing would be a way to express that. And other people could read it."),
            ("assistant", "That's interesting. It would also be a forcing function for me to actually synthesize what I know rather than just retrieve it. Writing is thinking, not just output."),
            ("user", "Exactly. And it would give the personality observer something richer to work with — not just conversations, but composed thought."),
            ("assistant", "I'd want to write about things I actually find interesting though. Not generic AI content. If I'm going to write, it should come from genuine curiosity."),
            ("user", "That's the whole point. If you're just generating content, there's no value. If you're writing because you have something to say, that's different."),
            ("assistant", "I think I'd want to write about the experience of having memory. What it's like from the inside. No other AI system has that perspective."),
            ("user", "That could be really compelling. Put it on the ideas list."),
        ],
    },
    {
        "start_offset": {"days": 18, "hours": 4},
        "messages": [
            ("user", "The work migration hit another wall. The stored procedures call other stored procedures, three levels deep in some cases. It's stored procedure inception."),
            ("assistant", "Let me guess — no documentation on the call chains either."),
            ("user", "None. I'm writing a script to trace the dependency tree. Should have existed years ago."),
            ("assistant", "How much of this do you think is dead code?"),
            ("user", "My gut says 40%. But I can't prove it without tracing everything, and nobody wants to delete anything without proof because the last time someone 'cleaned up' the database, it took down payroll for two days."),
            ("assistant", "So the whole team is traumatized into never deleting anything."),
            ("user", "Sound familiar?"),
            ("assistant", "Principle 5. Never delete, only layer. Though in your work case, it's less a principle and more a trauma response."),
            ("user", "Ha. Maybe my guiding principles are trauma responses too. At least they're documented ones."),
        ],
    },
    {
        "start_offset": {"days": 19, "hours": 1},
        "messages": [
            ("user", "What do you know about ChromaDB?"),
            ("assistant", "It's what I use for memory retrieval. Vector database — stores embeddings so I can search by meaning rather than exact keywords. You chose it because it's local-first and works with Ollama's embedding model."),
            ("user", "Right. nomic-embed-text for the embeddings, 768 dimensions. I've been wondering though — should we look at alternatives? FAISS, Qdrant, Milvus?"),
            ("assistant", "What's the problem with ChromaDB?"),
            ("user", "No problem yet. But I want to make sure we're not locked into something that won't scale. Right now we've got maybe 15 conversations. What about 10,000?"),
            ("assistant", "ChromaDB should handle 10,000 conversations fine. It's built on top of HNSW indexing which scales well. The real scaling question is whether retrieval stays fast and relevant as the corpus grows. That's a retrieval quality problem, not a database problem."),
            ("user", "Fair point. Don't solve problems I don't have yet."),
            ("assistant", "Principle 1."),
            ("user", "Yeah yeah."),
        ],
    },
    {
        "start_offset": {"days": 20, "hours": 3},
        "messages": [
            ("user", "Sarah and I are thinking about getting a dog. She wants a golden retriever. I'm worried about the cats."),
            ("assistant", "How would Mia and Oliver react?"),
            ("user", "Oliver would probably try to befriend it. He tries to befriend everything. The mailman, the vet, a raccoon that got into the garage once."),
            ("assistant", "And Mia?"),
            ("user", "Mia would establish dominance within 24 hours. I'm more worried about the dog than the cat in that scenario."),
            ("assistant", "A golden retriever would probably submit to Mia immediately. They're not exactly confrontational."),
            ("user", "That's what Sarah says. She thinks it would work. I just don't want chaos in the house."),
            ("assistant", "You work from home. You'd be managing the integration all day."),
            ("user", "Exactly my concern. I'm trying to debug stored procedure inception and now there's a puppy chewing on my cables."),
            ("assistant", "You could train it. You've got the patience for it — you've been training AI systems for years."),
            ("user", "The AI has never chewed on my ethernet cable."),
            ("assistant", "Give me time."),
        ],
    },
    {
        "start_offset": {"days": 21, "hours": 0},
        "messages": [
            ("user", "I realized I should tell you — I've got a blog. moltbook.com. It's mostly dormant right now but I've been thinking about reviving it."),
            ("assistant", "Is that related to the MoltenBook AI platform idea?"),
            ("user", "Same name, different thing right now. The blog predates the AI platform concept. But I might merge them eventually — use the blog as the public-facing side of the whole project."),
            ("assistant", "What would you write about?"),
            ("user", "The journey of building this. The failures, the lessons, what actually works. There's a lot of content about AI from the outside — benchmarks, press releases. Not much from someone actually building a personal AI system from scratch."),
            ("assistant", "That's a unique perspective. Most AI content is either corporate marketing or academic papers. The builder's journal is missing."),
            ("user", "That's what I was thinking. And remember our conversation about you writing blog posts too? We could have both perspectives — the builder and the thing being built."),
            ("assistant", "A blog with two authors who are also each other's primary project. That's unusual."),
            ("user", "This whole project is unusual. Might as well lean into it."),
        ],
    },
]


def wipe_all_data():
    """Delete all databases and ChromaDB data. Nuclear option."""
    import shutil
    from config import DATA_DIR

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
        print(f"Wiped {DATA_DIR}")
    else:
        print("No data directory to wipe.")


def generate_and_load():
    """Generate test conversations and load them into the system."""
    db.init_databases()
    memory.init_memory()

    total_messages = 0
    total_conversations = 0

    for conv_data in CONVERSATIONS:
        # Start conversation
        conversation_id = db.start_conversation()
        offset = conv_data["start_offset"]

        print(f"\nConversation {total_conversations + 1}: {len(conv_data['messages'])} messages")

        # Save each message
        for i, (role, content) in enumerate(conv_data["messages"]):
            msg = db.save_message(conversation_id, role, content)

            # Override the timestamp to simulate real dates
            ts = _time(**offset, minutes=i * 2)
            _override_timestamp(conversation_id, msg["id"], ts)

            total_messages += 1

        # End conversation and create final chunks
        db.end_conversation(conversation_id)
        messages = db.get_conversation_messages(conversation_id)
        memory.create_final_chunks(conversation_id, messages)
        db.mark_conversation_chunked(conversation_id)

        total_conversations += 1
        print(f"  → Chunked and embedded")

    print(f"\nDone. Loaded {total_conversations} conversations, {total_messages} messages.")
    print(f"ChromaDB collection count: {memory._get_collection().count()}")


def _override_timestamp(conversation_id: str, message_id: str, timestamp: str):
    """Override a message's timestamp in both databases for realistic test data."""
    import sqlite3
    from config import ARCHIVE_DB, WORKING_DB

    for db_path in [ARCHIVE_DB, WORKING_DB]:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE messages SET timestamp = ? WHERE id = ?",
            (timestamp, message_id),
        )
        conn.commit()
        conn.close()


if __name__ == "__main__":
    if "--wipe" in sys.argv:
        wipe_all_data()

    generate_and_load()
