"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    profile_path: Path
    ring_signing_key: str | None
    ring_api_base: str
    ring_access_token: str | None
    counter: str  # "bedrock" | "simulated" | "motion"
    bedrock_model_id: str
    aws_region: str
    sim_truth_path: Path | None

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("PULSE_HOME", "."))
        truth = os.getenv("PULSE_SIM_TRUTH")
        return cls(
            db_path=Path(os.getenv("PULSE_DB", root / "pulse.db")),
            profile_path=Path(os.getenv("PULSE_PROFILE", root / "profiles" / "demo_cafe.yml")),
            ring_signing_key=os.getenv("RING_HMAC_KEY"),
            ring_api_base=os.getenv("RING_API_BASE", "https://api.amazonvision.com"),
            ring_access_token=os.getenv("RING_ACCESS_TOKEN"),
            counter=os.getenv("PULSE_COUNTER", "motion"),
            bedrock_model_id=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            sim_truth_path=Path(truth) if truth else None,
        )
