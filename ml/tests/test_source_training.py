import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("ultralytics")

from train.source_training import SourceBCE, SourceValidator, supervised_classes  # noqa: E402


def test_unknown_classes_have_zero_gradient_without_losing_known_negative_gradient():
    logits = torch.zeros(2, 3, 5, requires_grad=True)
    targets = torch.zeros_like(logits)
    targets[0, 0, 1] = 1
    targets[1, 1, 4] = 1
    loss = SourceBCE()
    loss.coverage = torch.tensor([[[0, 1, 1, 0, 0]], [[0, 0, 0, 0, 1]]])
    loss(logits, targets).sum().backward()
    assert torch.count_nonzero(logits.grad[0, :, [0, 3, 4]]) == 0
    assert torch.count_nonzero(logits.grad[1, :, [0, 1, 2, 3]]) == 0
    assert logits.grad[0, 0, 1] < 0  # положительная каска
    assert logits.grad[0, 1, 1] > 0  # настоящий негатив для каски
    assert logits.grad[1, 1, 4] < 0  # положительная сигарета


def test_validation_does_not_count_unannotated_classes_as_false_positives(monkeypatch):
    captured = []

    def parent_update(self, preds, batch):
        captured.extend(preds)

    monkeypatch.setattr(
        "ultralytics.models.yolo.detect.val.DetectionValidator.update_metrics", parent_update
    )
    validator = SourceValidator.__new__(SourceValidator)
    pred = {
        "cls": torch.tensor([1.0, 3.0, 4.0]),
        "conf": torch.tensor([0.8, 0.9, 0.7]),
        "bboxes": torch.zeros(3, 4),
    }
    validator.update_metrics([pred], {"im_file": ["cigarette-000001.jpg"]})
    assert captured[0]["cls"].tolist() == [4.0]
    assert captured[0]["bboxes"].shape == (1, 4)


def test_unknown_source_fails_instead_of_silently_enabling_all_negatives():
    with pytest.raises(ValueError, match="unknown supervision"):
        supervised_classes("untracked-image.jpg")
