# Optional SAGE integration

This adapter belongs to the [SAGE project family](PROJECT_FAMILY.md). The main SAGE code is in [l33tdawg/sage](https://github.com/l33tdawg/sage/). Lantern-specific product apps, device orchestration and integrations will live in a separate planned repository; the existing adapter is retained here during that transition.

The transport, signed public-resource and offline envelope modules require no SAGE service or SDK. The optional adapter contains the protected inbox, durable sealed capture and WorkflowStore interface.

Its source controls were developed against `sage-agent-sdk==11.19.10+lantern.20260906`. That Lantern SDK build must be supplied as a separately built, compatible local wheel. Do not substitute an unrelated public SDK version and treat it as qualified.

```sh
python -m pip install /path/to/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl
python -m pip install -e '.[dev,sage]'
python -m pytest tests/integrations/sage
```

These controls use SDK MockTransport and modeled native ownership. They prove source behavior only. Real encrypted backend, consent revocation, owner loss, boot/deadline recovery and physical worker qualification remain required before enabling private service dispatch. The public Linux launcher refuses private MESSAGE grants.

SAGE messages retain their existing protocol identity and sender/recipient pinning. Moving the code does not enroll identities, rotate keys, import received packs into SAGE, produce read receipts or establish a canonical SAGE commit. The service owner must supply current authorization and protected storage.
