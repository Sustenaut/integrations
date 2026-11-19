// cucumber-js's setup for these scenarios: pubtest's steps (its GRAMMAR.md), and a
// step may take as long as a test could under node:test.
import * as cucumber from "@cucumber/cucumber";
import { register } from "@sustenaut/pubtest";
import { mock } from "node:test";

cucumber.setDefaultTimeout(60_000);

// The service under test of pubtest's HTTP steps is the one a Given chose (this.serve).
register(cucumber, {
  client: (world) => (method, path, headers, body) => {
    if (!world.serve) throw new Error("no service was chosen: a Given sets the one the HTTP steps send to");
    return world.serve(method, path, headers, body);
  },
});

// What a scenario mocks with node:test (console, timers, globalThis.fetch) is put back.
cucumber.After(function () {
  mock.timers.reset();
  mock.restoreAll();
});
