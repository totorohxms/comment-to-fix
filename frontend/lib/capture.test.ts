// @vitest-environment jsdom
// Capture engine: selector paths and instrumentation buffers.

import { beforeEach, describe, expect, it } from "vitest";
import { cssPath } from "./capture";

describe("cssPath", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("prefers ids", () => {
    document.body.innerHTML = `<div><button id="btn-edit">Edit</button></div>`;
    expect(cssPath(document.getElementById("btn-edit")!)).toBe("#btn-edit");
  });

  it("builds class + nth-of-type paths without ids", () => {
    document.body.innerHTML = `
      <section class="card head">
        <button class="btn">a</button>
        <button class="btn">b</button>
      </section>`;
    const second = document.querySelectorAll("button")[1];
    const path = cssPath(second);
    expect(path).toContain("button.btn:nth-of-type(2)");
    expect(document.querySelector(path)).toBe(second);   // selector resolves back
  });

  it("anchors at the nearest ancestor id", () => {
    document.body.innerHTML = `<div id="root"><span class="x">hi</span></div>`;
    const span = document.querySelector("span")!;
    expect(cssPath(span)).toBe("#root > span.x");
  });

  it("skips widget-internal ctf- classes", () => {
    document.body.innerHTML = `<div class="ctf-panel real"><p class="ctf-x note">n</p></div>`;
    const p = document.querySelector("p")!;
    expect(cssPath(p)).not.toContain("ctf-");
    expect(cssPath(p)).toContain("p.note");
  });
});
