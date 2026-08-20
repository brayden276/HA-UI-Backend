import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const publish = args.includes("--publish");
const level = args.find(arg => ["patch", "minor", "major"].includes(arg)) ?? "patch";
const git = process.platform === "win32" ? "C:\\Program Files\\Git\\cmd\\git.exe" : "git";
const readJson = path => JSON.parse(readFileSync(join(root, path), "utf8"));
const writeJson = (path, value) => writeFileSync(join(root, path), `${JSON.stringify(value, null, 2)}\n`);
const run = (command, commandArgs, options = {}) => execFileSync(command, commandArgs, { cwd: root, stdio: "inherit", ...options });

const packageJson = readJson("package.json");
const manifestPath = "custom_components/ha_component_backend/manifest.json";
const manifest = readJson(manifestPath);
const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(packageJson.version);
if (!match) throw new Error(`Unsupported package version: ${packageJson.version}`);
let [major, minor, patch] = match.slice(1).map(Number);
if (level === "major") [major, minor, patch] = [major + 1, 0, 0];
if (level === "minor") [minor, patch] = [minor + 1, 0];
if (level === "patch") patch += 1;
const version = `${major}.${minor}.${patch}`;

if (dryRun) {
  console.log(`Would prepare HA Component Backend v${version}${publish ? " and publish it" : ""}.`);
  process.exit(0);
}

const status = execFileSync(git, ["status", "--porcelain"], { cwd: root, encoding: "utf8" }).trim();
if (status) throw new Error("Release requires a clean working tree.");

packageJson.version = version;
manifest.version = version;
writeJson("package.json", packageJson);
writeJson(manifestPath, manifest);
run("node", ["scripts/check.mjs"]);

if (!publish) {
  console.log(`Prepared HA Component Backend v${version}. Review, commit, tag and push it to publish through HACS.`);
  process.exit(0);
}

run(git, ["add", "--", "package.json", manifestPath]);
run(git, ["commit", "-m", `Release v${version}`]);
run(git, ["tag", `v${version}`]);
run(git, ["push", "origin", "HEAD"]);
run(git, ["push", "origin", `v${version}`]);
console.log(`Published HA Component Backend v${version}. HACS will offer the release after GitHub processes the tag.`);
