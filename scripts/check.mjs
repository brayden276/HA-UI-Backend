import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const readJson = path => JSON.parse(readFileSync(join(root, path), "utf8"));
const fail = message => { throw new Error(message); };
const packageJson = readJson("package.json");
const hacs = readJson("hacs.json");
const componentsRoot = join(root, "custom_components");

if (!existsSync(componentsRoot)) fail("custom_components directory is missing");
const domains = readdirSync(componentsRoot, { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name);
if (domains.length !== 1) fail(`HACS integrations require exactly one custom_components domain; found ${domains.length}`);

const domain = domains[0];
const componentPath = join("custom_components", domain);
const manifest = readJson(join(componentPath, "manifest.json"));
for (const key of ["domain", "name", "documentation", "issue_tracker", "codeowners", "version"]) {
  if (!manifest[key] || (Array.isArray(manifest[key]) && manifest[key].length === 0)) fail(`manifest.json is missing ${key}`);
}
if (manifest.domain !== domain) fail(`manifest domain ${manifest.domain} must match ${componentPath}`);
if (manifest.version !== packageJson.version) fail(`package.json version ${packageJson.version} must match manifest version ${manifest.version}`);
if (!hacs.name) fail("hacs.json is missing name");
if (!existsSync(join(root, componentPath, "services.yaml"))) fail("services.yaml is missing");

const pythonFiles = readdirSync(join(root, componentPath), { withFileTypes: true }).filter(entry => entry.isFile() && entry.name.endsWith(".py")).map(entry => join(componentPath, entry.name));
execFileSync("python", ["-c", "from pathlib import Path; import sys; [compile(Path(path).read_text(encoding='utf-8'), path, 'exec') for path in sys.argv[1:]]", ...pythonFiles], { cwd: root, stdio: "inherit" });

console.log(`HACS integration check passed: ${domain} (${pythonFiles.length} Python modules)`);
