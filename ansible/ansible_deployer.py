"""
automation/ansible_deployer.py
================================
Fourth deployment method: Ansible + NAPALM idempotent fabric changes.

The FastAPI gateway calls AnsibleDeployer.deploy(intent) which:
  1. Writes the intent to a temp JSON file
  2. Invokes the ibn_orchestrator.yml playbook via subprocess
  3. Returns structured DeploymentResult with Ansible return code + stdout

Install requirements:
  pip install ansible ansible-lint
  ansible-galaxy collection install arista.eos
  ansible-galaxy collection install ansible.netcommon
"""

import asyncio, json, os, subprocess, tempfile, time
from models.intent_models import CanonicalIntent, DeploymentResult

ANSIBLE_DIR   = os.getenv("ANSIBLE_DIR",   "ansible")
INVENTORY     = os.getenv("ANSIBLE_INVENTORY", "ansible/inventory/digital_twin.yml")
PLAYBOOK      = os.getenv("ANSIBLE_PLAYBOOK",  "ansible/playbooks/ibn_orchestrator.yml")
VAULT_PASS    = os.getenv("ANSIBLE_VAULT_PASS_FILE", "")


class AnsibleDeployer:

    async def deploy(self, intent: CanonicalIntent,
                     dry_run: bool = False) -> DeploymentResult:
        t0 = time.time()

        # Write intent to temp file (playbook reads it via -e @file)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(intent.dict(), f)
            payload_file = f.name

        try:
            result = await self._run_playbook(intent.intent_id,
                                               payload_file, dry_run)
        finally:
            os.unlink(payload_file)

        duration = int((time.time() - t0) * 1000)
        success  = result.returncode == 0

        return DeploymentResult(
            intent_id     = intent.intent_id,
            method        = "ansible" + ("_check" if dry_run else ""),
            nodes_updated = self._parse_changed_hosts(result.stdout),
            flows_pushed  = 0,
            status        = "success" if success else "failed",
            detail        = (
                f"Ansible rc={result.returncode} | "
                f"duration={duration}ms\n"
                + result.stdout[-500:] if result.stdout else ""
            )
        )

    async def _run_playbook(self, intent_id: str,
                             payload_file: str,
                             dry_run: bool) -> subprocess.CompletedProcess:
        cmd = [
            "ansible-playbook",
            PLAYBOOK,
            "-i", INVENTORY,
            "-e", f"intent_id={intent_id}",
            "-e", f"@{payload_file}",
            "-e", "require_simulation_pass=true",
        ]
        if dry_run:
            cmd.append("--check")
        if VAULT_PASS:
            cmd += ["--vault-password-file", VAULT_PASS]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=os.getcwd()
        )
        stdout, _ = await proc.communicate()
        return type("Result", (), {
            "returncode": proc.returncode,
            "stdout": stdout.decode() if stdout else ""
        })

    def _parse_changed_hosts(self, stdout: str) -> list[str]:
        """Extract changed host names from Ansible PLAY RECAP."""
        import re
        hosts   = []
        for line in stdout.splitlines():
            m = re.match(r"^(\S+)\s+:\s+ok=\d+\s+changed=(\d+)", line)
            if m and int(m.group(2)) > 0:
                hosts.append(m.group(1))
        return hosts

    async def check_mode(self, intent: CanonicalIntent) -> DeploymentResult:
        """Run ansible --check (dry-run, no changes applied)."""
        return await self.deploy(intent, dry_run=True)
