"""OS-admin setup: python -m bscli.analytics.admin --home PATH configure|check."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from getpass import getpass
import json
import os
from pathlib import Path

from bscli.analytics.contracts import BUSINESS_TZ, Budget, Query
from bscli.core.capability_runtime import CapabilityRejected
from bscli.core.data_sources import DataSourceConfig
from bscli.core.data_source_secrets import DataSourceSecretStore
from bscli.database.postgres_read import PostgresReadExecutor


def main():
    parser = argparse.ArgumentParser(description="在中心服务运行身份下配置加密的分析数据源；密码仅通过隐藏交互读取。")
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("action", choices=["configure", "check"])
    args = parser.parse_args()
    root = args.home / "analytics"
    store = DataSourceSecretStore(root / "credentials")
    config = DataSourceConfig.load(root / "source.json")
    if args.action == "configure":
        previous_version = config.credential_version
        username = input("数据库只读用户名：").strip()
        password = getpass("数据库密码：")
        if not username or not password:
            parser.error("用户名及密码必填")
        config = replace(config, username=username, enabled=False, privileges_reviewed=False,
                         credential_version=config.credential_version + 1)
        store.save(f"{config.source_id}:{config.credential_version}", {"password": password})
        del password
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / "source.json"
        temporary = root / "source.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(config), stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
        store.delete(f"{config.source_id}:{previous_version}")
        print("凭据已加密保存，数据源保持关闭。请完成权限检查和试点准入。")
        return
    today = datetime.now(BUSINESS_TZ).date()
    query = Query(today-timedelta(days=1), today, "none")
    try:
        # Empty ID set cannot select business rows. This is a metadata/contract check.
        PostgresReadExecutor(store).summarize(config, {"principal_id": "1", "rows": []}, query, Budget())
    except CapabilityRejected as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "message": exc.message}, ensure_ascii=False))
        raise SystemExit(1)
    print("数据库最小契约检查通过；业务权限和真实用户验收仍需独立完成。")


if __name__ == "__main__":
    main()
