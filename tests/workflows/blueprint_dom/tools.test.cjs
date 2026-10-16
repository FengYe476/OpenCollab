"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test } = require("node:test");
const { openBlueprint } = require("./harness.cjs");

const source = `entry: lead
roles:
  lead:
    prompt: Coordinate and deliver the task.
    tools: [file_read, submit, adopt, spawn_agent, adopt_agent]
  coder:
    prompt: Implement the task.
    tools: [file_read, submit]
topology:
  lead: [coder]
`;
const categories = { adopt: "work", submit: "work", adopt_agent: "coord" };
const basicSource = source
  .replace("file_read, submit, adopt, spawn_agent, adopt_agent", "file_read, spawn_agent")
  .replace("file_read, submit", "file_read");

test("delivery and adoption tools import and export as known tools", t => {
  const blueprint = openBlueprint(t, source);
  const loaded = blueprint.resolvedConfig(blueprint.source);

  assert.equal(blueprint.document.querySelectorAll(".issue.error").length, 0);
  assert.equal(blueprint.document.querySelectorAll(".chip.unknown").length, 0);
  assert.ok(blueprint.document.querySelector(".stats .pill.ok"));
  assert.deepEqual(blueprint.resolvedConfig(blueprint.saveExport()), loaded);
  assert.deepEqual([...blueprint.exported().roles.lead.tools], loaded.roles.lead.tools);
});

test("delivery and adoption menu tools can be added, removed, and added again", t => {
  const blueprint = openBlueprint(t, basicSource.replace("file_read, spawn_agent", "file_read"));
  const expected = blueprint.resolvedConfig(blueprint.source);
  blueprint.click('[data-act="edit"]');

  for (const [name, category] of Object.entries(categories)) {
    const selector = '[data-tool-role="lead"][data-tool="' + name + '"]';
    const chip = () => blueprint.document.querySelector(selector);
    assert.ok(chip(), name + " is available in the menu");
    assert.ok(chip().classList.contains(category), name + " uses its runtime category");
    assert.equal(chip().getAttribute("aria-pressed"), "false");

    blueprint.click(selector);
    assert.equal(chip().getAttribute("aria-pressed"), "true");
    assert.ok(blueprint.exported().roles.lead.tools.includes(name));
    blueprint.click(selector);
    assert.equal(chip().getAttribute("aria-pressed"), "false");
    assert.equal(blueprint.exported().roles.lead.tools.includes(name), false);
    blueprint.click(selector);
    assert.equal(chip().getAttribute("aria-pressed"), "true");
    assert.equal(blueprint.document.querySelectorAll(selector).length, 1);
    expected.roles.lead.tools.push(name);
  }

  assert.equal(blueprint.document.querySelectorAll(".issue").length, 0);
  assert.deepEqual(blueprint.resolvedConfig(blueprint.saveExport()), expected);
});

test("the tool menu matches the runtime registry and its categories", t => {
  const repo = path.resolve(__dirname, "../../..");
  const python = process.env.OPENCOLLAB_TEST_PYTHON || path.join(repo, ".venv/bin/python");
  const registry = JSON.parse(execFileSync(python, ["-c", [
    "import json",
    "from opencollab.bootstrap.tool_registry import (",
    "    STATELESS_TOOL_FACTORIES, SCHEDULER_TOOL_FACTORIES, SKILL_TOOL_FACTORIES)",
    "print(json.dumps({'work': sorted(STATELESS_TOOL_FACTORIES),",
    "                  'coord': sorted(SCHEDULER_TOOL_FACTORIES),",
    "                  'skill': sorted(SKILL_TOOL_FACTORIES)}))",
  ].join("\n")], { env: { ...process.env, PYTHONPATH: repo }, encoding: "utf8" }));
  const blueprint = openBlueprint(t, source);
  blueprint.click('[data-act="edit"]');

  for (const [category, names] of Object.entries(registry)) {
    const chips = blueprint.document.querySelectorAll('[data-tool-role="lead"].' + category);
    assert.deepEqual([...chips].map(chip => chip.getAttribute("data-tool")).sort(), names);
  }
  assert.equal(blueprint.document.querySelectorAll('[data-tool-role="lead"]').length,
    Object.values(registry).flat().length);
});

test("an unknown role tool remains an error until removed", t => {
  const blueprint = openBlueprint(t, basicSource.replace("file_read, spawn_agent",
    "file_read, spawn_agent, unknown_tool"));
  const errors = [...blueprint.document.querySelectorAll(".issue.error")];
  assert.equal(errors.length, 1);
  assert.match(errors[0].textContent, /unknown tool 'unknown_tool'/);
  assert.equal(blueprint.document.querySelector(".stats .pill.ok"), null);
  assert.equal(blueprint.document.querySelector(".chip.unknown").textContent, "unknown_tool");

  blueprint.click('[data-act="edit"]');
  blueprint.click('[data-tool-role="lead"][data-tool="unknown_tool"]');
  assert.equal(blueprint.document.querySelectorAll(".issue.error").length, 0);
  assert.equal(blueprint.document.querySelector('[data-tool-role="lead"][data-tool="unknown_tool"]'), null);
  assert.deepEqual(blueprint.resolvedConfig(blueprint.saveExport()).roles.lead.tools,
    ["file_read", "spawn_agent"]);
});

for (const name of ["adopt_agent", "spawn_agent", "unknown_tool"]) {
  test(`tool limits still reject ${name} with the runtime's reason`, t => {
    const blueprint = openBlueprint(t, basicSource + `tool_limits:\n  ${name}: {}\n`);
    const errors = [...blueprint.document.querySelectorAll(".issue.error")];
    assert.equal(errors.length, 1);
    assert.equal(errors[0].querySelector(".who").textContent, "tool_limits");
    const coordination = name !== "unknown_tool";
    assert.match(errors[0].textContent, coordination
      ? new RegExp("not supported for coordination tool '" + name + "'")
      : /names unknown tool 'unknown_tool'/);
    assert.equal(blueprint.document.querySelector(".stats .pill.ok"), null);
    assert.throws(
      () => blueprint.resolvedConfig(blueprint.source),
      error => (coordination ? /not supported for coordination tools/ : /names unknown tools/)
        .test(String(error.stderr)),
    );
    assert.throws(
      () => blueprint.resolvedConfig(blueprint.saveExport()),
      error => (coordination ? /not supported for coordination tools/ : /names unknown tools/)
        .test(String(error.stderr)),
    );
  });
}
