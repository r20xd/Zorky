"""Обучение одной YOLO на источниках с частичной разметкой разных классов.

Отрицательный classification loss считается только по классам, которые
размечает источник кадра. Смешивающие разные кадры аугментации отключены.
Требует рабочего ML-окружения, не минимального gate-окружения.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import copy
from pathlib import Path

import torch
from ultralytics.models.yolo.detect.train import DetectionTrainer
from ultralytics.models.yolo.detect.val import DetectionValidator
from ultralytics.nn.tasks import DetectionModel
from ultralytics.utils.loss import v8DetectionLoss

SOURCE_CLASSES = {"helmet": (1, 2), "cigarette": (4,), "fangchen": (3,)}


def supervised_classes(filename: str) -> tuple[int, ...]:
    source = Path(filename).stem.split("-", 1)[0]
    if source not in SOURCE_CLASSES:
        raise ValueError(f"unknown supervision source: {source}")
    return SOURCE_CLASSES[source]


class SourceBCE(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.coverage = None

    def forward(self, predictions, targets):
        if self.coverage is None or self.coverage.shape[0] != predictions.shape[0]:
            raise ValueError("missing per-image class supervision")
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            predictions, targets, reduction="none"
        )
        return loss * self.coverage.to(predictions)


class SourceDetectionLoss(v8DetectionLoss):
    def __init__(self, model):
        super().__init__(model)
        if self.nc != 5:
            raise ValueError("source-aware loss expects the five existing model outputs")
        self.bce = SourceBCE()

    def get_assigned_targets_and_loss(self, preds, batch, **kwargs):
        names = batch["im_file"]
        coverage = torch.zeros(len(names), 1, self.nc, device=self.device)
        for i, name in enumerate(names):
            coverage[i, 0, list(supervised_classes(name))] = 1
        self.bce.coverage = coverage
        return super().get_assigned_targets_and_loss(preds, batch, **kwargs)


def source_criterion(model):
    if getattr(model.model[-1], "one2one_cv2", None) is not None:
        raise ValueError("only the existing YOLO11 detection head is supported")
    return SourceDetectionLoss(model)


@contextmanager
def source_loss_enabled():
    # Класс модели остаётся стандартным DetectionModel. В checkpoint не
    # сохраняются пользовательские классы модели или training-only criterion.
    original = DetectionModel.init_criterion
    DetectionModel.init_criterion = source_criterion
    try:
        yield
    finally:
        DetectionModel.init_criterion = original


class SourceValidator(DetectionValidator):
    def update_metrics(self, preds, batch):
        filtered = []
        for pred, filename in zip(preds, batch["im_file"], strict=True):
            allowed = torch.tensor(supervised_classes(filename), device=pred["cls"].device)
            mask = torch.isin(pred["cls"], allowed)
            filtered.append({key: value[mask] for key, value in pred.items()})
        return super().update_metrics(filtered, batch)


class SourceTrainer(DetectionTrainer):
    def get_model(self, cfg=None, weights=None, verbose=True):
        target_names = self.data["names"]
        # Сохраняем все строки существующей головы, включая обученный respirator.
        # После загрузки строка 3 получает расширенное значение face_protection.
        self.data["names"] = {**target_names, 3: "respirator"}
        try:
            model = super().get_model(cfg, weights, verbose)
        finally:
            self.data["names"] = target_names
        model.names = target_names
        return model

    def get_validator(self):
        return SourceValidator(
            self.test_loader,
            save_dir=self.save_dir,
            args=copy(self.args),
            _callbacks=self.callbacks,
        )
