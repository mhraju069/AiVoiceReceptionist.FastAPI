import os

def load_knowledge() -> str:
    """
    Loads knowledge base text from the data directory.
    """
    # Look for knowledge.txt in data/ or root directory
    paths_to_try = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "knowledge.txt"),
        os.path.join(os.getcwd(), "data", "knowledge.txt"),
        "data/knowledge.txt",
        "knowledge.txt"
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return "Voca AI is an ultra-premium AI receptionist that assists businesses in handling calls efficiently."
