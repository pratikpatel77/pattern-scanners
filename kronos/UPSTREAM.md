# Upstream provenance

Reviewed repository: https://github.com/shiyu-coder/Kronos
Reviewed master commit: `67b630e67f6a18c9e9be918d9b4337c960db1e9a`.

Kronos tokenizes OHLCV market sequences into hierarchical discrete tokens,
predicts tokens autoregressively, then decodes and denormalizes future candles.
It is a generative forecasting model; breakout labeling and trade simulation
are application logic, not native model guarantees.

| Model | Official model ID | Tokenizer | Context capacity |
|---|---|---|---:|
| mini | NeoQuasar/Kronos-mini | NeoQuasar/Kronos-Tokenizer-2k | 2048 |
| small | NeoQuasar/Kronos-small | NeoQuasar/Kronos-Tokenizer-base | 512 |
| base | NeoQuasar/Kronos-base | NeoQuasar/Kronos-Tokenizer-base | 512 |

This UI limits context to 512 bars for consistent resource bounds. The model
loader records actual checkpoint revisions, device and Torch version in each
result, together with input checksum, seed and saved settings.

The bundled `kronos_model` retains the upstream architecture and inference path.
Its `predict_samples` adapter returns individual decoded scenarios. Upstream
`predict(sample_count > 1)` averages paths; averaging must not be used to estimate
breakout frequencies or forecast bands. The application therefore uses raw paths
or repeated one-sample predictions. No training or fine-tuning is performed.

Relevant upstream sources reviewed: README, model/kronos.py, requirements.txt,
examples and regression tests. Dependencies include Torch, pandas, numpy,
einops, huggingface_hub and safetensors. The original scanner's own dependencies
are reused for cache and detector access.

Kronos is MIT licensed; see `../kronos_model/LICENSE`.
The locally bundled Plotly.js chart library is MIT licensed, copyright Plotly,
Inc.; its license notice is retained in `static/vendor/plotly.min.js`.

Limitations: sample frequencies are uncalibrated; checkpoints may have seen
historical evaluation data; decoded OHLC bounds may need repair; there is no
exchange-calendar model; and daily cached candles cannot resolve intrabar order.
These limitations are surfaced in the dashboard and usage guide.
