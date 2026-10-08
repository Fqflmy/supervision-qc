# -*- coding: utf-8 -*-
"""把 dompurify 同步进 frontend/package-lock.json。

为什么需要
----------
dompurify 是通过脚本直接解包安装的（本机 npm 的落盘步骤会被静默丢弃），
因此 package-lock.json 中没有对应条目。而 CI 使用 `npm ci`，它**要求
package.json 与 lock 文件严格一致**，缺条目会直接报错失败。

npm 自身的 reify 在本机不可用，因此这里按 lockfileVersion 3 的格式手工补齐：
- packages[""].dependencies 增加 dompurify
- packages["node_modules/dompurify"] 增加版本/下载地址/完整性校验

用法：
    python backend/scripts/sync_frontend_lock.py
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\Docment\supervision-qc")
FRONTEND = ROOT / "frontend"
LOCK = FRONTEND / "package-lock.json"
PACKAGE_JSON = FRONTEND / "package.json"
REGISTRY = "https://registry.npmmirror.com"


def registry_dist(name: str, version: str) -> dict:
    """取指定版本的 dist 信息（tarball 与 integrity）。"""
    with urllib.request.urlopen(f"{REGISTRY}/{name}/{version}", timeout=60) as response:
        meta = json.loads(response.read().decode("utf-8"))
    return meta["dist"]


def main() -> int:
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    packages = lock.setdefault("packages", {})

    deps = pkg.get("dependencies", {})
    changed: list[str] = []

    for name, spec in sorted(deps.items()):
        key = f"node_modules/{name}"
        if key in packages:
            continue
        # package.json 里是范围（如 ^3.4.16），lock 需要确切版本
        installed_json = FRONTEND / "node_modules" / name / "package.json"
        if not installed_json.exists():
            print(f"  [WARN] {name} 未安装于 node_modules，跳过")
            continue
        installed = json.loads(installed_json.read_text(encoding="utf-8"))
        version = installed["version"]
        dist = registry_dist(name, version)
        packages[key] = {
            "version": version,
            "resolved": dist["tarball"],
            "integrity": dist["integrity"],
            "license": installed.get("license", ""),
            "dependencies": installed.get("dependencies", {}),
            "engines": installed.get("engines", {}),
        }
        # 清理空字段，保持与 npm 生成格式接近
        packages[key] = {k: v for k, v in packages[key].items() if v}
        deps[name] = spec
        changed.append(f"{name}@{version}")

    root_pkg = packages.setdefault("", {})
    root_pkg["dependencies"] = dict(sorted(deps.items()))
    dev_deps = pkg.get("devDependencies", {})
    if dev_deps:
        root_pkg["devDependencies"] = dict(sorted(dev_deps.items()))

    if not changed:
        print("  package-lock.json 已是最新，无需变更")
        return 0

    LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"  已补齐 {len(changed)} 个条目: {', '.join(changed)}")
    print(f"  lockfileVersion = {lock.get('lockfileVersion')}")
    print(f"  packages 条目数 = {len(packages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
