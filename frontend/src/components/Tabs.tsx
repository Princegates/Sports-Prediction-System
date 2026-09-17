interface Props {
  tabs: string[];
  active: string;
  onChange: (tab: string) => void;
}

export function Tabs({ tabs, active, onChange }: Props) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab}
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
