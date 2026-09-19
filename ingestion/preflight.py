"""Read-only model availability checks; never substitute a model."""

import httpx


async def model_catalogue(config, secrets):
    provider = config["interpreter"]
    base = (
        config["helmcode_base_url"]
        if provider == "deepseek"
        else config["jev_base_url"]
    )
    model = config["deepseek_model"] if provider == "deepseek" else config["jev_model"]
    key = secrets["HELMCODE_API_KEY" if provider == "deepseek" else "JEV_API_KEY"]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"}
        )
    if response.status_code >= 400:
        raise ValueError(
            f"{provider} model catalogue HTTP {response.status_code}; check endpoint and credential"
        )
    try:
        catalogue = response.json()
        entries = catalogue.get("data", catalogue.get("models", []))
        available = {
            entry.get("id", entry.get("name")) if isinstance(entry, dict) else entry
            for entry in entries
        }
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Provider model catalogue format unsupported") from exc
    if model not in available:
        raise ValueError(
            "Configured model is absent from account catalogue; choose an explicit available model ID"
        )
    return {"provider": provider, "model": model, "catalogue_verified": True}
