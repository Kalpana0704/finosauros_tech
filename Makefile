.PHONY: up serve backfill test

up:
	docker compose up --build

serve:
	python -m concall serve

backfill:
	python -m concall backfill

test:
	pytest -q
