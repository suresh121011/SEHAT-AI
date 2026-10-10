"use client";

import { useState, type ReactNode } from "react";

export function Tabs({ tabs }: { tabs: { id: string; label: string; content: ReactNode }[] }) {
  const [activeTab, setActiveTab] = useState(tabs[0]?.id);

  return (
    <div className="w-full">
      <div className="flex space-x-1 border-b border-subtle mb-4">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-semibold transition-all duration-200 border-b-2 ${
              activeTab === tab.id
                ? "border-[#0891B2] text-[#0891B2]"
                : "border-transparent text-muted hover:text-[#164E63] hover:border-[#164E63]/30"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="mt-2">
        {tabs.find((t) => t.id === activeTab)?.content}
      </div>
    </div>
  );
}
