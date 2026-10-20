import assert from "node:assert/strict";
import test from "node:test";
import { includesNotice } from "./license-notices.mjs";

test("license verification accepts Git line endings without weakening notice checks", () => {
  const notice = "MIT License\n\nCopyright (c) Owner\nPermission is hereby granted.\n";
  for (const source of [notice, notice.replace(/\n/g, "\r\n")]) {
    for (const rendered of [notice, notice.replace(/\n/g, "\r\n")]) {
      assert.ok(includesNotice(`/*!\n${rendered}*/`, source));
    }
  }
  assert.equal(includesNotice(notice.replace("Copyright (c) Owner\n", ""), notice), false);
  assert.equal(includesNotice(notice.replace("hereby granted", "NOT granted"), notice), false);
});
