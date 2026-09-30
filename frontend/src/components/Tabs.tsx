import { useEffect, useRef } from "react";

interface Props {
  tabs: string[];
  active: string;
  onChange: (tab: string) => void;
}

export function Tabs({ tabs, active, onChange }: Props) {
  const activeRef = useRef<HTMLButtonElement | null>(null);

  // Keeps the active tab in view within the horizontally-scrolling strip --
  // needed once tabs can change by swiping the panel below (MatchDetail),
  // not just by tapping a tab button that's already on screen.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [active]);

  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab}
          ref={tab === active ? activeRef : undefined}
          role="tab"
          aria-selected={tab === active}
          className={`tab-button${tab === active ? " active" : ""}`}
          onClick={() => onChange(tab)}
        >
          {tab}
        </button>
      ))}
    </div>
  );
}
