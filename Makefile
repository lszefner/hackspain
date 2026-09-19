PYTHON ?= python3
ERP_PORT ?= 8009
ERP_HOST ?= 127.0.0.1
LOTE2_ERP ?= ../lote_2_sorpresa/erp_export_lote2.csv
ERP := $(PYTHON) alberto_erp.py --puerto $(ERP_PORT)

.DEFAULT_GOAL := help
POLICY ?= balanced
SCAN_INPUT ?= rules_ingestion/eval/fixtures
.PHONY: help erp erp-fast erp-lote2 erp-lote2-fast erp-status erp-login rules rules-offline rules-all-policies scan scan-gen test eval-fixtures eval-score eval-score-live e2e-fixtures e2e-score e2e-score-live

help: ## Show participant commands.
	@printf '%s\n' '500 Sombras de Alberto' '' 'Commands:'
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

erp: ## Start the local ERP on port 8009.
	$(ERP)

erp-fast: ## Start the local ERP without artificial latency.
	$(ERP) --rapido

erp-lote2: ## Start the ERP with the Saturday data update.
	$(ERP) --lote2 "$(LOTE2_ERP)"

erp-lote2-fast: ## Start the updated ERP without artificial latency.
	$(ERP) --lote2 "$(LOTE2_ERP)" --rapido

erp-status: ## Check that the local ERP is running.
	@curl --fail --silent --show-error "http://$(ERP_HOST):$(ERP_PORT)/erp/estado"
	@printf '\n'

erp-login: ## Request and print a local ERP session token.
	@curl --fail --silent --show-error -X POST "http://$(ERP_HOST):$(ERP_PORT)/erp/login" \
		-d 'usuario=alberto' -d 'clave=FACTURAS2009'
	@printf '\n'

rules: ## Build outcome/<ver>/$(POLICY)/rules.json (POLICY=balanced|strict|conservative).
	$(PYTHON) -m rules_ingestion.build_rules --policy "$(POLICY)"

rules-offline: ## Same, fully offline (no JEV / LLM).
	$(PYTHON) -m rules_ingestion.build_rules --policy "$(POLICY)" --no-jev --no-llm

rules-all-policies: ## Build balanced + strict + conservative offline.
	$(PYTHON) -m rules_ingestion.build_rules --policy balanced --no-jev --no-llm
	$(PYTHON) -m rules_ingestion.build_rules --policy strict --no-jev --no-llm
	$(PYTHON) -m rules_ingestion.build_rules --policy conservative --no-jev --no-llm

scan: ## Discover rules in SCAN_INPUT; fold into outcome/<ver>/$(POLICY)/rules.json.
	$(PYTHON) -m rules_ingestion.build_rules --policy "$(POLICY)" --scan "$(SCAN_INPUT)"

scan-gen: ## Discover + codegen for NEW rules.
	$(PYTHON) -m rules_ingestion.build_rules --policy "$(POLICY)" --scan "$(SCAN_INPUT)" --gen-code

test: eval-score ## Alias: run eval offline score (baseline + traps + structural).

eval-fixtures: ## Regenerate seeded eval fixtures (baseline / traps / structural).
	$(PYTHON) -m rules_ingestion.eval.generate_fixtures

eval-score: ## Score full pipeline offline against eval fixtures.
	$(PYTHON) -m rules_ingestion.eval.score_pipeline --regen

eval-score-live: ## Same with live JEV + DeepSeek (needs keys).
	$(PYTHON) -m rules_ingestion.eval.score_pipeline --regen --live

# Back-compat aliases
e2e-fixtures: eval-fixtures
e2e-score: eval-score
e2e-score-live: eval-score-live

