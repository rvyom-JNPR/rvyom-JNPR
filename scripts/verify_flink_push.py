#!/usr/bin/env python3
"""
Analyze JSON diff from a GitHub PR to identify added, updated, and removed keys.
Optionally verify that Flink views are actually running in the target clusters.

Usage:
    python pr_json_diff.py <repo> <pr_number> [--env <environment>] [--verify] [--json]
    
Examples:
    python pr_json_diff.py mistsys/versions 10917
    python pr_json_diff.py mistsys/versions 10917 --env gcp.mec1prod6 --verify
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed


# Base path for biloba-config repo
BILOBA_CONFIG_REPO = Path.home() / "work" / "biloba-config"


def get_job_config_from_tag(job_name: str, env: str, tag: str) -> dict | None:
    """Get job config from biloba-config repo at a specific tag."""
    config_path = f"leavenworth/{env}/{job_name}.conf"
    
    result = subprocess.run(
        ["git", "-C", str(BILOBA_CONFIG_REPO), "show", f"{tag}:{config_path}"],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        return None
    
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def get_job_cluster_from_config(config: dict) -> str | None:
    """Extract kubernetes namespace (cluster) from job config."""
    return config.get("execution", {}).get("kubernetes_namespace")


def get_job_configs_with_versions(jobs_with_versions: dict[str, dict], env: str) -> dict[str, dict]:
    """
    Get configs for jobs using each job's specific BILOBA_CONFIG_VER tag.
    
    Args:
        jobs_with_versions: Dict of job_name -> {"BILOBA_CONFIG_VER": "v0.0.xxxx", "BILOBA_VER": "..."}
        env: Environment name (e.g., gcp.mec1prod6)
    
    Returns:
        Dict of job_name -> config dict
    """
    job_configs = {}
    
    for job_name, versions in jobs_with_versions.items():
        tag = versions.get("BILOBA_CONFIG_VER")
        if not tag:
            continue
        
        config = get_job_config_from_tag(job_name, env, tag)
        if config:
            job_configs[job_name] = {
                "config": config,
                "tag": tag,
            }
    
    return job_configs


def get_unique_clusters_from_configs(job_configs: dict[str, dict]) -> set[str]:
    """Extract unique cluster names from job configs."""
    clusters = set()
    for job_data in job_configs.values():
        config = job_data.get("config") if isinstance(job_data, dict) and "config" in job_data else job_data
        cluster = get_job_cluster_from_config(config)
        if cluster:
            clusters.add(cluster)
    return clusters


def get_pr_files(repo: str, pr_number: int) -> list[dict]:
    """Get list of files changed in the PR."""
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["files"]


def get_file_content_at_ref(repo: str, file_path: str, ref: str) -> str | None:
    """Get file content at a specific git ref."""
    result = subprocess.run(
        ["gh", "api", f"/repos/{repo}/contents/{file_path}?ref={ref}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    
    data = json.loads(result.stdout)
    if data.get("encoding") == "base64":
        import base64
        return base64.b64decode(data["content"]).decode("utf-8")
    return data.get("content")


def get_pr_refs(repo: str, pr_number: int) -> tuple[str, str]:
    """Get base and head refs for a PR."""
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "baseRefOid,headRefOid"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["baseRefOid"], data["headRefOid"]


def flatten_json(obj: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten nested JSON into dot-notation keys."""
    items = {}
    for key, value in obj.items():
        new_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.update(flatten_json(value, new_key))
        else:
            items[new_key] = value
    return items


def compare_json(old_data: dict | None, new_data: dict | None) -> dict:
    """Compare two JSON objects and return differences."""
    old_flat = flatten_json(old_data) if old_data else {}
    new_flat = flatten_json(new_data) if new_data else {}
    
    old_keys = set(old_flat.keys())
    new_keys = set(new_flat.keys())
    
    added = {}
    updated = {}
    removed = {}
    
    # Added keys
    for key in new_keys - old_keys:
        added[key] = new_flat[key]
    
    # Removed keys
    for key in old_keys - new_keys:
        removed[key] = old_flat[key]
    
    # Updated keys
    for key in old_keys & new_keys:
        if old_flat[key] != new_flat[key]:
            updated[key] = {"old": old_flat[key], "new": new_flat[key]}
    
    return {"added": added, "updated": updated, "removed": removed}


def get_top_level_changes(old_data: dict | None, new_data: dict | None) -> dict:
    """Get changes at the top-level keys only."""
    old_keys = set(old_data.keys()) if old_data else set()
    new_keys = set(new_data.keys()) if new_data else set()
    
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    
    # Keys that exist in both but have any changes
    updated = []
    for key in sorted(old_keys & new_keys):
        if old_data.get(key) != new_data.get(key):
            updated.append(key)
    
    return {"added": added, "updated": updated, "removed": removed}


def print_summary(file_path: str, changes: dict, detailed_changes: dict) -> None:
    """Print a summary of changes."""
    print(f"\n{'='*60}")
    print(f"File: {file_path}")
    print(f"{'='*60}")
    
    # Top-level summary
    print(f"\n📊 Summary:")
    print(f"  • Added:   {len(changes['added'])} top-level keys")
    print(f"  • Updated: {len(changes['updated'])} top-level keys") 
    print(f"  • Removed: {len(changes['removed'])} top-level keys")
    
    # Added keys
    if changes["added"]:
        print(f"\n✅ ADDED ({len(changes['added'])} keys):")
        for key in changes["added"]:
            print(f"  + {key}")
    
    # Updated keys
    if changes["updated"]:
        print(f"\n🔄 UPDATED ({len(changes['updated'])} keys):")
        for key in changes["updated"]:
            print(f"  ~ {key}")
            # Show detailed changes for this key
            for detail_key, detail in detailed_changes["updated"].items():
                if detail_key.startswith(key + ".") or detail_key == key:
                    print(f"      {detail_key.split('.')[-1]}: {detail['old']} → {detail['new']}")
    
    # Removed keys
    if changes["removed"]:
        print(f"\n❌ REMOVED ({len(changes['removed'])} keys):")
        for key in changes["removed"]:
            print(f"  - {key}")


def output_json(results: list[dict]) -> None:
    """Output results as JSON for programmatic use."""
    print(json.dumps(results, indent=2))


def get_flink_jobmanager_url(cluster_name: str, env: str) -> str:
    """Construct the Flink JobManager URL for a cluster."""
    # Extract short env name: gcp.mec1prod6 -> mec1prod6
    env_short = env.split(".")[-1] if "." in env else env
    return f"http://{cluster_name}-{env_short}.mist.pvt"


def get_running_jobs_from_cluster(cluster_name: str, env: str, timeout: int = 5) -> tuple[str, list[str], str | None]:
    """Get list of running job names from a Flink cluster."""
    url = get_flink_jobmanager_url(cluster_name, env)
    jobs_url = f"{url}/jobs/overview"
    
    try:
        req = urllib.request.Request(jobs_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            running_jobs = [job["name"] for job in data.get("jobs", []) if job.get("state") == "RUNNING"]
            return cluster_name, running_jobs, None
    except urllib.error.URLError as e:
        return cluster_name, [], f"Connection error: {e.reason}"
    except TimeoutError:
        return cluster_name, [], "Timeout"
    except json.JSONDecodeError:
        return cluster_name, [], "Invalid JSON response"
    except Exception as e:
        return cluster_name, [], str(e)


def get_all_running_jobs(clusters: set[str], env: str) -> dict[str, list[str]]:
    """Get running jobs from specified clusters in parallel."""
    running_jobs_by_cluster: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    
    print(f"\n🔍 Querying {len(clusters)} Flink clusters...")
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_running_jobs_from_cluster, cluster, env): cluster
            for cluster in clusters
        }
        
        for future in as_completed(futures):
            cluster_name, jobs, error = future.result()
            if error:
                errors[cluster_name] = error
            else:
                running_jobs_by_cluster[cluster_name] = jobs
    
    if errors:
        print(f"\n⚠️  Failed to query {len(errors)} clusters:")
        for cluster, error in sorted(errors.items()):
            print(f"   • {cluster}: {error}")
    
    total_jobs = sum(len(jobs) for jobs in running_jobs_by_cluster.values())
    print(f"✅ Found {total_jobs} running jobs across {len(running_jobs_by_cluster)} clusters")
    
    return running_jobs_by_cluster


def verify_jobs_running(
    changes: dict,
    job_configs: dict[str, dict],
    running_jobs_by_cluster: dict[str, list[str]],
) -> dict:
    """Verify that added/updated jobs are actually running in the expected clusters."""
    all_running_jobs = set()
    for jobs in running_jobs_by_cluster.values():
        all_running_jobs.update(jobs)
    
    verification_results = {
        "added": {"running": [], "not_running": [], "no_config": []},
        "updated": {"running": [], "not_running": [], "no_config": []},
        "removed": {"still_running": [], "confirmed_stopped": [], "no_config": []},
    }
    
    # Check added jobs
    for job_name in changes["added"]:
        job_data = job_configs.get(job_name)
        if not job_data:
            verification_results["added"]["no_config"].append(job_name)
        else:
            config = job_data.get("config", job_data)
            tag = job_data.get("tag", "unknown")
            expected_cluster = get_job_cluster_from_config(config)
            if job_name in all_running_jobs:
                verification_results["added"]["running"].append({
                    "job": job_name,
                    "expected_cluster": expected_cluster,
                    "tag": tag,
                    "found_in_cluster": next(
                        (c for c, jobs in running_jobs_by_cluster.items() if job_name in jobs),
                        None
                    ),
                })
            else:
                verification_results["added"]["not_running"].append({
                    "job": job_name,
                    "expected_cluster": expected_cluster,
                    "tag": tag,
                })
    
    # Check updated jobs
    for job_name in changes["updated"]:
        job_data = job_configs.get(job_name)
        if not job_data:
            verification_results["updated"]["no_config"].append(job_name)
        else:
            config = job_data.get("config", job_data)
            tag = job_data.get("tag", "unknown")
            expected_cluster = get_job_cluster_from_config(config)
            if job_name in all_running_jobs:
                verification_results["updated"]["running"].append({
                    "job": job_name,
                    "expected_cluster": expected_cluster,
                    "tag": tag,
                    "found_in_cluster": next(
                        (c for c, jobs in running_jobs_by_cluster.items() if job_name in jobs),
                        None
                    ),
                })
            else:
                verification_results["updated"]["not_running"].append({
                    "job": job_name,
                    "expected_cluster": expected_cluster,
                    "tag": tag,
                })
    
    # Check removed jobs (they should NOT be running)
    for job_name in changes["removed"]:
        job_data = job_configs.get(job_name)
        if job_data:
            config = job_data.get("config", job_data)
            expected_cluster = get_job_cluster_from_config(config)
        else:
            expected_cluster = None
        
        if job_name in all_running_jobs:
            verification_results["removed"]["still_running"].append({
                "job": job_name,
                "expected_cluster": expected_cluster,
                "found_in_cluster": next(
                    (c for c, jobs in running_jobs_by_cluster.items() if job_name in jobs),
                    None
                ),
            })
        else:
            verification_results["removed"]["confirmed_stopped"].append(job_name)
    
    return verification_results


def print_verification_results(verification: dict) -> None:
    """Print verification results as a table."""
    print(f"\n{'='*100}")
    print("🔬 FLINK JOB VERIFICATION RESULTS")
    print(f"{'='*100}")
    
    # Collect all rows for the table
    rows = []
    
    # Added jobs
    for item in verification["added"]["running"]:
        status = "✓ Running" if item["expected_cluster"] == item["found_in_cluster"] else "⚠ Wrong Cluster"
        rows.append(("ADDED", item["job"], item.get("tag", "-"), item["expected_cluster"] or "-", item["found_in_cluster"] or "-", status))
    for item in verification["added"]["not_running"]:
        rows.append(("ADDED", item["job"], item.get("tag", "-"), item["expected_cluster"] or "-", "-", "✗ NOT Running"))
    for job in verification["added"]["no_config"]:
        rows.append(("ADDED", job, "-", "-", "-", "? No Config"))
    
    # Updated jobs
    for item in verification["updated"]["running"]:
        status = "✓ Running" if item["expected_cluster"] == item["found_in_cluster"] else "⚠ Wrong Cluster"
        rows.append(("UPDATED", item["job"], item.get("tag", "-"), item["expected_cluster"] or "-", item["found_in_cluster"] or "-", status))
    for item in verification["updated"]["not_running"]:
        rows.append(("UPDATED", item["job"], item.get("tag", "-"), item["expected_cluster"] or "-", "-", "✗ NOT Running"))
    for job in verification["updated"]["no_config"]:
        rows.append(("UPDATED", job, "-", "-", "-", "? No Config"))
    
    # Removed jobs
    for job in verification["removed"]["confirmed_stopped"]:
        rows.append(("REMOVED", job, "-", "-", "-", "✓ Stopped"))
    for item in verification["removed"]["still_running"]:
        rows.append(("REMOVED", item["job"], "-", item["expected_cluster"] or "-", item["found_in_cluster"] or "-", "⚠ Still Running"))
    for job in verification["removed"]["no_config"]:
        rows.append(("REMOVED", job, "-", "-", "-", "? No Config"))
    
    if rows:
        # Calculate column widths
        headers = ("Change", "Job Name", "Config Tag", "Expected Cluster", "Actual Cluster", "Status")
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        
        # Print table header
        header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
        separator = "-+-".join("-" * w for w in widths)
        print(f"\n{header_line}")
        print(separator)
        
        # Print rows
        for row in rows:
            row_line = " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
            print(row_line)
    
    # Summary
    added = verification["added"]
    updated = verification["updated"]
    removed = verification["removed"]
    
    print(f"\n{'='*100}")
    print("📊 VERIFICATION SUMMARY:")
    total_added = len(added["running"]) + len(added["not_running"]) + len(added["no_config"])
    total_updated = len(updated["running"]) + len(updated["not_running"]) + len(updated["no_config"])
    total_removed = len(removed["still_running"]) + len(removed["confirmed_stopped"]) + len(removed["no_config"])
    
    if total_added > 0:
        print(f"   Added:   {len(added['running'])}/{total_added} running")
    if total_updated > 0:
        print(f"   Updated: {len(updated['running'])}/{total_updated} running")
    if total_removed > 0:
        print(f"   Removed: {len(removed['confirmed_stopped'])}/{total_removed} stopped")
    
    # Exit code hint
    issues = len(added["not_running"]) + len(updated["not_running"]) + len(removed["still_running"])
    if issues > 0:
        print(f"\n⚠️  {issues} potential issues found!")
    else:
        print(f"\n✅ All verifications passed!")


def get_available_envs() -> list[str]:
    """List available environments from biloba-config."""
    leavenworth_path = BILOBA_CONFIG_REPO / "leavenworth"
    if not leavenworth_path.exists():
        return []
    return sorted([d.name for d in leavenworth_path.iterdir() if d.is_dir() and not d.name.startswith(".")])


def parse_args():
    """Parse command line arguments."""
    available_envs = get_available_envs()
    
    parser = argparse.ArgumentParser(
        description="Analyze JSON diff from a GitHub PR and optionally verify Flink jobs"
    )
    parser.add_argument("repo", help="GitHub repository (e.g., mistsys/versions)")
    parser.add_argument("pr_number", type=int, help="Pull request number")
    parser.add_argument(
        "--env",
        help=f"Environment to verify against (e.g., gcp.mec1prod6). Available: {', '.join(available_envs[:5])}...",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify that jobs are running in Flink clusters",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    return parser.parse_args()


def extract_env_from_filepath(file_path: str) -> str | None:
    """Extract environment from versions file path like biloba/gcp-mec1prod6.json -> gcp.mec1prod6."""
    # biloba/gcp-mec1prod6.json -> gcp-mec1prod6 -> gcp.mec1prod6
    import re
    match = re.search(r'biloba/([^/]+)\.json$', file_path)
    if match:
        env_name = match.group(1)
        # Convert gcp-mec1prod6 to gcp.mec1prod6
        return env_name.replace("-", ".", 1)
    return None


def main():
    args = parse_args()
    
    print(f"Analyzing PR #{args.pr_number} in {args.repo}...")
    
    # Get PR details
    files = get_pr_files(args.repo, args.pr_number)
    base_ref, head_ref = get_pr_refs(args.repo, args.pr_number)
    
    json_files = [f for f in files if f["path"].endswith(".json")]
    
    if not json_files:
        print("No JSON files changed in this PR.")
        sys.exit(0)
    
    results = []
    
    for file_info in json_files:
        file_path = file_info["path"]
        
        # Get old and new content
        old_content = get_file_content_at_ref(args.repo, file_path, base_ref)
        new_content = get_file_content_at_ref(args.repo, file_path, head_ref)
        
        old_data = json.loads(old_content) if old_content else None
        new_data = json.loads(new_content) if new_content else None
        
        # Compare
        top_level_changes = get_top_level_changes(old_data, new_data)
        detailed_changes = compare_json(old_data, new_data)
        
        result = {
            "file": file_path,
            "top_level": top_level_changes,
            "detailed": detailed_changes,
        }
        
        # Determine environment from file path or --env argument
        env = args.env or extract_env_from_filepath(file_path)
        
        # Verify jobs if requested
        if args.verify and env and new_data:
            # Build dict of job -> {BILOBA_CONFIG_VER, BILOBA_VER} for all changed jobs
            jobs_with_versions = {}
            
            # For added and updated jobs, use versions from new_data
            for job_name in top_level_changes["added"] + top_level_changes["updated"]:
                if job_name in new_data:
                    jobs_with_versions[job_name] = new_data[job_name]
            
            # For removed jobs, use versions from old_data (to find their expected cluster)
            for job_name in top_level_changes["removed"]:
                if old_data and job_name in old_data:
                    jobs_with_versions[job_name] = old_data[job_name]
            
            print(f"\n📁 Reading job configs from biloba-config for {env}...")
            print(f"   (using each job's BILOBA_CONFIG_VER tag)")
            job_configs = get_job_configs_with_versions(jobs_with_versions, env)
            print(f"✅ Found configs for {len(job_configs)}/{len(jobs_with_versions)} jobs")
            
            # Get unique clusters to query
            clusters = get_unique_clusters_from_configs(job_configs)
            
            if clusters:
                running_jobs = get_all_running_jobs(clusters, env)
                verification = verify_jobs_running(top_level_changes, job_configs, running_jobs)
                result["verification"] = verification
                
                if not args.json:
                    print_summary(file_path, top_level_changes, detailed_changes)
                    print_verification_results(verification)
            else:
                print("⚠️  No clusters found in job configs")
                if not args.json:
                    print_summary(file_path, top_level_changes, detailed_changes)
        elif args.verify and not env:
            print("⚠️  --env is required for verification (or use a biloba/*.json file path)")
            if not args.json:
                print_summary(file_path, top_level_changes, detailed_changes)
        else:
            if not args.json:
                print_summary(file_path, top_level_changes, detailed_changes)
        
        results.append(result)
    
    if args.json:
        output_json(results)


if __name__ == "__main__":
    main()
