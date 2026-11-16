// cucumber-js's setup for these scenarios: a step may take as long as a test
// could under node:test.
import { setDefaultTimeout } from "@cucumber/cucumber";

setDefaultTimeout(60_000);
