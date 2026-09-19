__version__ = "1.0"

__all__ = ["DecisionEngine", "decide_files"]


def __getattr__(name):
    if name == "DecisionEngine":
        from .engine import DecisionEngine

        return DecisionEngine
    if name == "decide_files":
        from .io import decide_files

        return decide_files
    raise AttributeError(name)
