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

const splitRegistry = readFileSync(join(root, componentPath, "split_registry.py"), "utf8");
const storage = readFileSync(join(root, componentPath, "storage.py"), "utf8");
const services = readFileSync(join(root, componentPath, "services.py"), "utf8");
const websocket = readFileSync(join(root, componentPath, "websocket.py"), "utf8");
const diagnostics = readFileSync(join(root, componentPath, "diagnostics.py"), "utf8");
const saveIndex = storage.indexOf("await store.async_save(next_data)");
const publishIndex = storage.indexOf("return next_data, True");
if (saveIndex < 0 || publishIndex < 0 || saveIndex > publishIndex) {
  fail("Store mutations must persist before publishing the new document");
}
if (!splitRegistry.includes("self.data = next_data")) fail("Split registry does not publish committed Store data");
if ((services.match(/supports_response=SupportsResponse\.OPTIONAL/g) ?? []).length !== 9) {
  fail("Every mutation service must expose an optional acknowledged response");
}
for (const command of [
  "preferences/get",
  "preferences/update",
  "preferences/remove",
  "profile/get",
  "profile/update",
  "profile/remove",
  "energy/day",
]) {
  if (!websocket.includes(command.toUpperCase().replaceAll("/", "_"))) {
    fail(`WebSocket preference command is not registered: ${command}`);
  }
}
if (!diagnostics.includes('logging.getLogger("custom_components.ha_component_backend")')) {
  fail("Backend diagnostics must write through the central integration logger");
}
for (const file of ["__init__.py", "services.py", "websocket.py", "energy.py"]) {
  const source = readFileSync(join(root, componentPath, file), "utf8");
  if (!source.includes(".diagnostics import")) {
    fail(`${file} must route boundary failures through central diagnostics`);
  }
}

const pythonFiles = readdirSync(join(root, componentPath), { withFileTypes: true }).filter(entry => entry.isFile() && entry.name.endsWith(".py")).map(entry => join(componentPath, entry.name));
execFileSync("python", ["-c", "from pathlib import Path; import sys; [compile(Path(path).read_text(encoding='utf-8'), path, 'exec') for path in sys.argv[1:]]", ...pythonFiles], { cwd: root, stdio: "inherit" });
execFileSync("python", ["scripts/test_storage_contract.py"], { cwd: root, stdio: "inherit" });
execFileSync("python", ["scripts/test_dashboard_contracts.py"], { cwd: root, stdio: "inherit" });
execFileSync("python", ["scripts/test_logging_contract.py"], { cwd: root, stdio: "inherit" });

console.log(`HACS integration check passed: ${domain} (${pythonFiles.length} Python modules)`);
