/** Picks the next version, updates the release files and writes release notes. */
import { execFileSync } from "node:child_process";
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";

const CHANGELOG_PATH = "CHANGELOG.md";
const MANIFEST_PATH = "custom_components/solar_energy_insights/manifest.json";
const UNRELEASED = "## Unreleased\n";

function parseVersion(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  if (!match) {
    throw new Error(`${version} is not a version like 1.2.3.`);
  }
  return match.slice(1).map(Number);
}

function compareVersions(a, b) {
  const [x, y] = [parseVersion(a), parseVersion(b)];
  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
}

function releasedVersions() {
  return execFileSync("git", ["tag", "--list", "v*"], { encoding: "utf8" })
    .split("\n")
    .map((tag) => tag.trim().slice(1))
    .filter((version) => /^\d+\.\d+\.\d+$/.test(version));
}

/**
 * The manifest's version, unless it is already released; then the next minor.
 * Setting the manifest to an unreleased version in a pull request releases
 * exactly that version, which is how to make a patch or major release.
 */
function nextVersion(current, released) {
  const latest = released.sort(compareVersions).at(-1);
  if (!released.includes(current)) {
    if (latest && compareVersions(current, latest) <= 0) {
      throw new Error(
        `manifest.json declares ${current}, which is not above the latest release ${latest}.`,
      );
    }
    return current;
  }
  const [major, minor] = parseVersion(latest);
  return `${major}.${minor + 1}.0`;
}

function release() {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));
  const version = nextVersion(manifest.version, releasedVersions());

  const changelog = readFileSync(CHANGELOG_PATH, "utf8");
  const start = changelog.indexOf(UNRELEASED);
  if (start === -1) {
    throw new Error("CHANGELOG.md must contain an Unreleased section.");
  }
  const from = start + UNRELEASED.length;
  const end = changelog.indexOf("\n## ", from);
  const notes = changelog.slice(from, end === -1 ? undefined : end).trim();

  const date = new Date().toISOString().slice(0, 10);
  writeFileSync(
    CHANGELOG_PATH,
    changelog.replace(
      UNRELEASED,
      `${UNRELEASED}\n## [${version}] - ${date}\n`,
    ),
  );
  manifest.version = version;
  writeFileSync(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`);
  writeFileSync("RELEASE_NOTES.md", notes ? `${notes}\n` : "");

  const output = process.env.GITHUB_OUTPUT;
  if (output) {
    appendFileSync(output, `tag=v${version}\n`);
  }
  console.log(`Releasing v${version}`);
}

release();
