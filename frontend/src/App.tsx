import { useEffect, useState } from "react";
import Header from "./components/Header";
import TabBar from "./components/TabBar";
import { tabFromHash, type TabId } from "./tabs";
import DocumentAssistantPage from "./tabs/document_assistant/DocumentAssistantPage";
import CircularAnalyzerPage from "./tabs/circular_analyzer/CircularAnalyzerPage";
import ComingSoonPage from "./tabs/coming_soon/ComingSoonPage";

const PAGES: Record<TabId, () => React.JSX.Element> = {
  "document-assistant": DocumentAssistantPage,
  "circular-analyzer": CircularAnalyzerPage,
  "coming-soon": ComingSoonPage,
};

export default function App() {
  const [active, setActive] = useState<TabId>(() => tabFromHash(window.location.hash));

  useEffect(() => {
    const onHash = () => setActive(tabFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const select = (id: TabId) => {
    window.location.hash = `/${id}`;
    setActive(id);
  };

  const Page = PAGES[active];

  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <TabBar active={active} onSelect={select} />
      <main
        id={`panel-${active}`}
        role="tabpanel"
        aria-labelledby={`tab-${active}`}
        className="mx-auto w-full max-w-6xl flex-1 px-6 py-12"
      >
        <Page />
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Punjab National Bank · For internal use only · All processing happens on this machine
      </footer>
    </div>
  );
}
