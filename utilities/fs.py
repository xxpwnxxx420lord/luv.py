import os

def view(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"No file found at: {abs_path}")
    if not os.path.isfile(abs_path):
        raise IsADirectoryError(f"Path is a directory, not a file: {abs_path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()