from .data import status_records
__all__ = ["ModelRegistry", "TrainingCancelled", "status_records", "train_file", "train_records"]


def __getattr__(name):
    if name == "ModelRegistry":
        from .registry import ModelRegistry
        return ModelRegistry
    if name in {"TrainingCancelled", "train_file", "train_records"}:
        from . import training
        return getattr(training, name)
    raise AttributeError(name)
