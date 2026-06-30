import { mkdir, open, type FileHandle } from "node:fs/promises";
import { dirname } from "node:path";
import type { PairedEvent } from "../format/types.js";
import type { EventHandler } from "../types.js";

export class JSONLHandler implements EventHandler {
  readonly path: string;
  private filePromise: Promise<FileHandle>;
  private chain: Promise<void> = Promise.resolve();

  constructor(path: string) {
    this.path = path;
    this.filePromise = this.openFile(path);
  }

  async onPairedEvent(event: PairedEvent): Promise<void> {
    const line = JSON.stringify(event) + "\n";
    this.chain = this.chain.then(async () => {
      const file = await this.filePromise;
      await file.write(line);
      await file.sync();
    });
    return this.chain;
  }

  async close(): Promise<void> {
    await this.chain;
    const file = await this.filePromise;
    await file.close();
  }

  private async openFile(path: string): Promise<FileHandle> {
    await mkdir(dirname(path), { recursive: true });
    return open(path, "w");
  }
}
