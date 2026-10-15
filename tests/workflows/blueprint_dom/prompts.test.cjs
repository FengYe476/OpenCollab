"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { openBlueprint } = require("./harness.cjs");

const prompt = 'Implement the frontend fixture with </script> and </ScRiPt>. '
  + 'Preserve <\\/script>, <\\/ScRiPt>, &lt;/script>, &amp;lt; and <main>HTML</main>. '
  + '\u4fdd\u7559\u539f\u6587 <!-- <script> </SCRIPT> '
  + '</script><script id="injected">window.injected = true</script>';
const prompts = {
  plain: prompt,
  single: "'" + prompt.replace(/'/g, "''") + "'",
  double: JSON.stringify(prompt),
  block: "|-\n      " + prompt,
};

for (const [style, value] of Object.entries(prompts)) {
  test(`${style} role prompts survive HTML generation and ordinary edits`, t => {
    const blueprint = openBlueprint(t, "roles:\n  lead:\n    prompt: " + value
      + "\n    model: fixture-model\n    temperature: 0.4\n    tools: [file_read, apply_patch]\n");
    const original = blueprint.resolvedConfig(blueprint.source);
    assert.equal(original.roles.lead.prompt, prompt);
    assert.equal(blueprint.document.querySelectorAll("script").length, 3);
    assert.equal(blueprint.document.querySelector("#injected"), null);
    assert.equal(blueprint.exported().roles.lead.prompt, prompt);
    assert.deepEqual(blueprint.resolvedConfig(blueprint.saveExport()), original);
    assert.equal(blueprint.document.querySelector(".prompt").textContent, prompt);

    blueprint.click('[data-act="edit"]');
    // Linkedom leaves textarea entities encoded; use its normal-text parser.
    const decoded = blueprint.document.createElement("div");
    decoded.innerHTML = blueprint.document.querySelector('[data-prompt="lead"]').innerHTML;
    assert.equal(decoded.textContent, prompt);
    blueprint.input('[data-temp="lead"]', "0.8");
    blueprint.click('[data-tool-role="lead"][data-tool="bash"]');
    const edited = blueprint.resolvedConfig(blueprint.saveExport());
    assert.equal(edited.roles.lead.prompt, prompt);
    assert.equal(edited.roles.lead.temperature, 0.8);
    assert.deepEqual(edited.roles.lead.tools, ["file_read", "apply_patch", "bash"]);
    assert.equal(blueprint.document.querySelector("#injected"), null);

    const replacement = prompt + "\nKeep </sCrIpT> in the updated fixture.";
    blueprint.input('[data-prompt="lead"]', replacement);
    blueprint.click('[data-act="edit"]');
    assert.equal(blueprint.document.querySelector(".prompt").textContent, replacement);
    assert.equal(blueprint.resolvedConfig(blueprint.saveExport()).roles.lead.prompt, replacement);
    assert.equal(blueprint.document.querySelector("#injected"), null);
  });
}
