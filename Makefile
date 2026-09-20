PYTHON ?= uv run python
ERP_PORT ?= 8009
ERP_HOST ?= 127.0.0.1
LOTE2_ERP ?= ../lote_2_sorpresa/erp_export_lote2.csv

# La Caja ya no esta suelta en la raiz. `caja/` es la copia viva (gitignored,
# se siembra con `make caja`) y `caja_de_alberto/vN/` son las instantaneas.
# INSTANTANEA coge la mas reciente ordenando por numero: `sort -V` para que
# v10 vaya despues de v9 y no antes, como haria el orden alfabetico.
INSTANTANEA := $(shell ls -d caja_de_alberto/v* 2>/dev/null | sort -V | tail -1)
ERP := $(PYTHON) caja/alberto_erp.py --puerto $(ERP_PORT)
# La centralita lee ELEVENLABS_API_KEY del entorno. Esto carga .env en la
# shell de la receta, sin que make lo parsee (un valor con espacios o con `#`
# lo rompe) y sin que quede nada en el propio Makefile.
CON_ENV := set -a; [ -f .env ] && . ./.env; set +a;

.DEFAULT_GOAL := help
POLICY ?= balanced
SCAN_INPUT ?= rules_ingestion/eval/fixtures
.PHONY: help centralita web-centralita export voces erp erp-fast erp-lote2 erp-lote2-fast erp-status erp-login api api-status rules rules-offline rules-all-policies scan scan-gen test eval-fixtures eval-score eval-score-live e2e-fixtures e2e-score e2e-score-live

help: ## Show participant commands.
	@printf '%s\n' '500 Sombras de Alberto' '' 'Commands:'
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# `caja` es el DIRECTORIO, no un phony: en cuanto existe, make lo da por hecho
# y no vuelve a copiarlo. Por eso los demas targets pueden depender de el sin
# resembrar nada. Para rehacerlo: rm -rf caja && make caja
caja: ## Seed the live Caja (caja/) from the newest snapshot.
	@test -n "$(INSTANTANEA)" || { echo "no hay ninguna captura en caja_de_alberto/"; exit 1; }
	@cp -R "$(INSTANTANEA)" caja
	@rm -f caja/MANIFIESTO.sha256 caja/PROCEDENCIA.md
	@echo "caja/ sembrada desde $(INSTANTANEA) ($$(ls caja/facturas | wc -l | tr -d ' ') facturas)"

export: ## Extract + evaluate the demo invoices offline into phone_calls/datos/.
	@$(CON_ENV) $(PYTHON) -m phone_calls.exportar

voces: ## Pre-render the demo's spoken lines (ElevenLabs if keyed, else `say`).
	@$(CON_ENV) $(PYTHON) -m phone_calls.voz

centralita: ## Voice agent for supplier calls, on port 8011 (needs `make export`).
	@test -f phone_calls/datos/maestro.json || { echo "falta el export: corre `make export`"; exit 1; }
	@# Con '-': sin `say` no hay audio pregrabado, pero la centralita arranca
	@# igual y habla el navegador. Quedarse sin demo por eso seria absurdo.
	-@$(CON_ENV) $(PYTHON) -m phone_calls.voz >/dev/null 2>&1
	@$(CON_ENV) $(PYTHON) -m phone_calls.servidor --puerto 8011

web-centralita: ## Freeze the centralita into frontend/ for Vercel (no network).
	@test -f phone_calls/datos/maestro.json || { echo "falta el export: corre `make export`"; exit 1; }
	@# NUNCA sintetiza: la clave de ElevenLabs tiene tope propio de 1.000
	@# caracteres y ya se agoto una vez. Copia lo que `make voces` dejo en
	@# .voz/ y avisa de lo que falte -- eso lo dira el navegador.
	@$(CON_ENV) $(PYTHON) -m phone_calls.publicar_web

erp: caja ## Start the local ERP on port 8009.
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

API_PORT ?= 8010
.PHONY: api-yaml
api: ## Start the engine JSON API on port 8010 (needs Supabase env; see docs/backend-api.md).
	@$(CON_ENV) uv run --locked --extra worker --extra backend python -m backend.server --port $(API_PORT)
api-yaml: ## Start API with current YAML/workbook rules compiled locally; contextual review disabled.
	@$(CON_ENV) uv run --locked --extra worker --extra backend python -m backend.yaml_api --port $(API_PORT)
api-status: ## Check that the engine API is running.
	@curl --fail --silent --show-error "http://127.0.0.1:$(API_PORT)/api/salud"
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
