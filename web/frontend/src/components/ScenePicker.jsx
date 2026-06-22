export default function ScenePicker({ scenes, scene, setScene, disabled }) {
  return (
    <div className="p-3 border-b border-viper-border">
      <div className="text-xs uppercase tracking-wider text-viper-muted mb-2">Scene</div>
      <div className="grid grid-cols-2 gap-2 max-h-[260px] overflow-y-auto pr-1">
        {scenes.map((s) => {
          const active = scene?.id === s.id;
          return (
            <button key={s.id} disabled={disabled} onClick={() => setScene(s)}
              className={`group relative rounded-lg overflow-hidden border text-left
                transition ${active ? "border-viper-accent ring-1 ring-viper-accent"
                                    : "border-viper-border hover:border-viper-muted"}
                disabled:opacity-50`}>
              <img src={s.image} alt={s.id}
                   className="w-full h-16 object-cover bg-black" />
              <div className="px-2 py-1">
                <div className="text-[11px] font-medium truncate">{s.id}</div>
                <div className="text-[10px] text-viper-muted">
                  {s.num_objects} obj · {s.is_3d ? "3D" : "2D"}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
