"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { openBlueprint } = require("./harness.cjs");

function team(existing = "coder", adHoc = "") {
  return `entry: lead
roles:
  lead:
    prompt: Coordinate the task.
    tools: [message_agent]
  ${JSON.stringify(existing)}:
    prompt: Implement the task.
    tools: [file_read]
  auditor:
    prompt: Review the task.
    tools: [file_read]
topology:
  lead: [${JSON.stringify(existing)}, auditor${adHoc ? ", " + adHoc : ""}]
`;
}

for (const name of ["coder", "CODER", "CoDeR"]) {
  test(`renaming another role to ${name} preserves the existing identity`, t => {
    const view = openBlueprint(t, team());
    const before = view.resolvedConfig(view.source);
    view.click('[data-act="edit"]');
    view.input('[data-rename="auditor"]', name, "change");
    assert.deepEqual(Object.keys(view.exported().roles), ["lead", "coder", "auditor"]);
    assert.deepEqual(view.resolvedConfig(view.saveExport()), before);
  });
}

for (const name of ["inspector", "AUDITOR"]) {
  test(`distinct or self case-only rename to ${name} remains valid`, t => {
    const view = openBlueprint(t, team());
    view.resolvedConfig(view.source);
    view.click('[data-act="edit"]');
    view.input('[data-rename="auditor"]', name, "change");
    assert.deepEqual(Object.keys(view.exported().roles), ["lead", "coder", name]);
    assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
    assert.deepEqual(view.resolvedConfig(view.saveExport()).topology.lead.sort(), ["coder", name].sort());
  });
}

test("ASCII collision checks preserve distinct non-ASCII identities", t => {
  const view = openBlueprint(t, team("kimi"));
  view.click('[data-act="edit"]');
  view.input('[data-rename="auditor"]', "kımi", "change");
  assert.ok(view.exported().roles.kımi);
  assert.ok(view.resolvedConfig(view.saveExport()).roles.kımi);
});

test("self case-only renames retain existing case-variant topology references", t => {
  const view = openBlueprint(t, team().replace("[\"coder\", auditor]", "[\"coder\", Auditor]"));
  const before = view.resolvedConfig(view.source);
  view.click('[data-act="edit"]');
  view.input('[data-rename="auditor"]', "AUDITOR", "change");
  assert.ok(view.exported().roles.AUDITOR);
  const loaded = view.resolvedConfig(view.saveExport());
  assert.deepEqual(loaded.topology.lead.sort(), ["AUDITOR", "coder"].sort());
  assert.deepEqual(loaded.roles.AUDITOR, before.roles.auditor);
  assert.ok(view.document.querySelector(".stats .pill.ok"));
});

test("case variants of referenced ad-hoc roles cannot merge topology nodes", t => {
  const view = openBlueprint(t, team("coder", "ghost"));
  const before = view.resolvedConfig(view.source);
  view.click('[data-act="edit"]');
  view.input('[data-rename="auditor"]', "GHOST", "change");
  assert.deepEqual([...view.exported().topology.lead], ["coder", "auditor", "ghost"]);
  assert.deepEqual(view.resolvedConfig(view.saveExport()), before);
});

test("loaded case-colliding roles are reported until one identity is repaired", t => {
  const view = openBlueprint(t, team().replaceAll("auditor", "CODER"));
  assert.throws(
    () => view.resolvedConfig(view.source),
    error => /role identities collide/.test(String(error.stderr)),
  );
  const errors = [...view.document.querySelectorAll(".issue.error")];
  assert.ok(errors.some(error => /coder/i.test(error.textContent) && /identity/i.test(error.textContent)));
  assert.equal(view.document.querySelector(".stats .pill.ok"), null);
  view.click('[data-act="edit"]');
  view.input('[data-rename="CODER"]', "auditor", "change");
  assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
  const loaded = view.resolvedConfig(view.saveExport());
  assert.ok(loaded.roles.auditor);
  assert.deepEqual(loaded.topology.lead.sort(), ["auditor", "coder"]);
});

test("new role names skip existing ASCII case variants", t => {
  const view = openBlueprint(t, team("ROLE"));
  view.resolvedConfig(view.source);
  view.click('[data-act="edit"]');
  view.click('[data-act="add-role"]');
  assert.ok(view.exported().roles.role2);
  view.input('[data-prompt="role2"]', "Inspect the task.");
  view.click('[data-tool-role="role2"][data-tool="file_read"]');
  view.change('[data-edge-src="lead"][data-edge-dst="role2"]', true);
  assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
  assert.ok(view.resolvedConfig(view.saveExport()).roles.role2);
});

const caseReferences = `entry: lead
roles:
  lead:
    prompt: Coordinate the task.
    tools: [message_agent]
  coder:
    prompt: Implement the task.
    tools: [file_read]
topology:
  Lead: [CODER]
`;

test("case-variant references use the declared role nodes and survive matrix edits", t => {
  const view = openBlueprint(t, caseReferences);
  const before = view.resolvedConfig(view.source);
  assert.deepEqual(before.topology.lead, ["coder"]);
  const edge = '[data-edge-src="lead"][data-edge-dst="coder"]';
  assert.ok(view.document.querySelector(edge).hasAttribute("checked"));
  assert.deepEqual([...view.document.querySelectorAll("[data-node]")].map(node => node.getAttribute("data-node")), ["lead", "coder"]);
  assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
  assert.deepEqual(view.resolvedConfig(view.saveExport()), before);

  view.change(edge, false);
  assert.deepEqual(view.resolvedConfig(view.saveExport()).topology, {});
  view.change(edge, true);
  assert.deepEqual(Object.keys(view.exported().topology), ["lead"]);
  assert.deepEqual(view.resolvedConfig(view.saveExport()), before);
});

for (const [kind, source] of [
  ["explicit", caseReferences.replace("entry: lead", "entry: LEAD")],
  ["implicit", caseReferences.replace("entry: lead\n", "").replace("  lead:\n", "  planner:\n").replace("  coder:\n", "  LEAD:\n").replace("Lead: [CODER]", "lead: [PLANNER]")],
]) {
  test(`${kind} entry references follow the declared role spelling`, t => {
    const view = openBlueprint(t, source);
    const before = view.resolvedConfig(view.source);
    assert.equal(view.exported().entry, before.entry);
    assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
    assert.deepEqual(view.resolvedConfig(view.saveExport()), before);
  });
}

test("renaming case-variant source, entry and target references retains their edges", t => {
  const view = openBlueprint(t, caseReferences.replace("entry: lead", "entry: LEAD"));
  const before = view.resolvedConfig(view.source);
  view.click('[data-act="edit"]');
  view.input('[data-rename="lead"]', "planner", "change");
  view.input('[data-rename="coder"]', "worker", "change");
  const loaded = view.resolvedConfig(view.saveExport());
  assert.equal(loaded.entry, "planner");
  assert.deepEqual(loaded.topology, { planner: ["worker"] });
  assert.deepEqual(loaded.roles, { planner: before.roles.lead, worker: before.roles.coder });
  assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
});

for (const removed of ["lead", "coder"]) {
  test(`removing ${removed} clears its case-variant references`, t => {
    const view = openBlueprint(t, caseReferences.replace("entry: lead", "entry: LEAD"));
    view.resolvedConfig(view.source);
    view.click('[data-act="edit"]');
    view.click(`[data-remove="${removed}"]`);
    const loaded = view.resolvedConfig(view.saveExport());
    assert.equal(loaded.entry, removed === "lead" ? "coder" : "lead");
    assert.deepEqual(loaded.topology, {});
    assert.deepEqual([...view.document.querySelectorAll("[data-node]")].map(node => node.getAttribute("data-node")), [loaded.entry]);
  });
}

for (const [topology, message, description = message] of [
  ["  Lead: [coder]\n  lead: [coder]\n", "source"],
  ["  Lead: []\n  lead: [coder]\n", "source", "source with an empty list"],
  ["  lead: [coder, CODER]\n", "destination"],
]) {
  test(`colliding topology ${description} references remain visible and rejected by the loader`, t => {
    const view = openBlueprint(t, caseReferences.replace("  Lead: [CODER]\n", topology));
    const rejectsCollision = error => new RegExp(`topology ${message} identities collide`).test(String(error.stderr));
    assert.throws(() => view.resolvedConfig(view.source), rejectsCollision);
    assert.deepEqual(view.exported().topology, view.original.topology);
    assert.throws(() => view.resolvedConfig(view.saveExport()), rejectsCollision);
    assert.ok([...view.document.querySelectorAll(".issue.error")].some(error => /topology.*identity/i.test(error.textContent)));
  });
}

test("renaming a role preserves the other colliding topology source", t => {
  const source = caseReferences.replace("  Lead: [CODER]\n", "  Lead: [coder]\n  lead: [ghost]\n");
  const view = openBlueprint(t, source);
  view.click('[data-act="edit"]');
  view.input('[data-rename="lead"]', "planner", "change");
  assert.deepEqual(Object.keys(view.exported().topology), ["Lead", "planner"]);
  assert.deepEqual(view.resolvedConfig(view.saveExport()).topology, { Lead: ["coder"], planner: ["ghost"] });
});

test("unknown entry and topology references retain their spelling", t => {
  const view = openBlueprint(t, caseReferences.replace("entry: lead", "entry: GHOST").replace("[CODER]", "[CODER, Ghost]"));
  assert.equal(view.exported().entry, "GHOST");
  assert.deepEqual([...view.exported().topology.lead], ["coder", "Ghost"]);
  assert.ok(view.document.querySelector('[data-node="Ghost"]'));
  assert.ok([...view.document.querySelectorAll(".issue.error")].some(error => /GHOST.*not declared/.test(error.textContent)));
  assert.throws(() => view.resolvedConfig(view.saveExport()), error => /entry role 'GHOST' is not declared/.test(String(error.stderr)));
});

for (const [label, lead, coder, sourceRef, targetRef] of [
  ["mixed-script aliases", "主Agent", "开发Agent", "主AGENT", "开发AGENT"],
  ["exact Chinese spelling", "主Agent", "开发Agent", "主Agent", "开发Agent"],
  ["ASCII aliases", "Lead", "Coder", "LEAD", "CODER"],
]) {
  const source = `entry: ${sourceRef}
roles:
  ${lead}:
    prompt: Coordinate the task.
    tools: [message_agent]
  ${coder}:
    prompt: Implement the task.
    tools: [file_read]
topology:
  ${sourceRef}: [${targetRef}]
`;

  test(`${label} follow renamed role definitions`, t => {
    const view = openBlueprint(t, source);
    const before = view.resolvedConfig(view.source);
    const nodes = [...view.document.querySelectorAll("[data-node]")].map(node => node.getAttribute("data-node"));
    view.click('[data-act="edit"]');
    view.input(`[data-rename="${coder}"]`, "worker", "change");
    const loaded = view.resolvedConfig(view.saveExport());
    assert.deepEqual(loaded.topology, { [lead]: ["worker"] });
    assert.deepEqual(loaded.roles.worker, before.roles[coder]);
    assert.deepEqual(nodes, [lead, coder]);
    assert.equal(loaded.entry, lead);
    assert.equal(view.document.querySelectorAll(".issue.error").length, 0);
  });

  for (const removed of [lead, coder]) {
    test(`${label} clear references when removing ${removed}`, t => {
      const view = openBlueprint(t, source);
      view.resolvedConfig(view.source);
      view.click('[data-act="edit"]');
      view.click(`[data-remove="${removed}"]`);
      const loaded = view.resolvedConfig(view.saveExport());
      const remaining = removed === lead ? coder : lead;
      assert.deepEqual(loaded.topology, {});
      assert.equal(loaded.entry, remaining);
      assert.deepEqual(Object.keys(loaded.roles), [remaining]);
      assert.deepEqual([...view.document.querySelectorAll("[data-node]")].map(node => node.getAttribute("data-node")), [remaining]);
    });
  }
}
