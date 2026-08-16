import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/graph.css";
import { WorkbenchApp } from "./app";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("kgdistiller frontend root is unavailable");
new WorkbenchApp(root).start();
