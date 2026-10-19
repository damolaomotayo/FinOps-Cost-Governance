# Optional shortcuts. 'make' just runs the commands below; each one is plain shell.
.PHONY: test deploy destroy demo verify guardrails
test:       ; .venv/bin/python -m pytest -q
deploy:     ; scripts/deploy.sh
demo:       ; .venv/bin/python scripts/demo_resources.py create
verify:     ; .venv/bin/python scripts/verify.py
guardrails: ; .venv/bin/python scripts/verify_guardrails.py
destroy:    ; scripts/destroy.sh
