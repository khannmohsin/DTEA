PYTHON ?= ./.venv/bin/python

.PHONY: install install-js clean clean-results clean-runtime clean-pycache test test-py test-js topology experiments report docker-down docker-reset

install:
	$(PYTHON) -m pip install -r requirements.txt

install-js:
	npm install
	cd node-registry-test && npm install

clean: clean-results clean-runtime clean-pycache

clean-results:
	rm -f results/experimental_results.json \
		results/latency.json \
		results/load_test.json \
		results/gas_summary.json \
		results/gas_comparison.json \
		results/contract_metrics.json \
		results/load_body_10.json \
		results/load_body_50.json \
		results/load_body_100.json

clean-runtime:
	rm -rf runtime/generated/*

clean-pycache:
	find Node_root scripts tests -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +

test: test-py test-js

test-py:
	$(PYTHON) -m pytest

test-js:
	cd node-registry-test && npx hardhat test

topology:
	$(PYTHON) scripts/run_topology.py --cloud 1 --fog 1 --edge 1 --endpoint 1 --scenario acm-fast

experiments:
	$(PYTHON) scripts/run_all_experiments.py

report:
	$(PYTHON) scripts/build_gas_comparison.py
	$(PYTHON) scripts/generate_matplotlib_report.py

docker-down:
	docker compose -f docker-compose.test.yml down -v --remove-orphans

docker-reset: docker-down
	docker system prune -af --volumes
