# Обёртка над tasks.py для Mac/Linux и CI. На Windows: py tasks.py <команда>
PYTHON ?= python3
TASKS := setup gate gate-ml gate-back gate-web demo e2e

.PHONY: $(TASKS)
$(TASKS):
	$(PYTHON) tasks.py $@
