import { imageUrl } from "../api";

export default function StageViewer({ event, scene }) {
  const src = event?.image_path ? imageUrl(event.image_path) : scene?.image;
  const m = event?.payload?.metrics;

  return (
    <div className="p-3 border-b border-viper-border min-h-0 flex flex-col">
      <div className="text-xs uppercase tracking-wider text-viper-muted mb-2 flex justify-between">
        <span>Viewer</span>
        {event && <span className="text-viper-muted/70 normal-case">{event.type}</span>}
      </div>

      <div className="rounded-lg overflow-hidden border border-viper-border bg-black grid place-items-center"
           style={{ aspectRatio: "16/9" }}>
        {src ? (
          <img key={src} src={src} alt="stage"
               className="w-full h-full object-contain animate-slidein" />
        ) : (
          <div className="text-viper-muted text-xs">no frame yet</div>
        )}
      </div>

      {event && (
        <div className="mt-2 text-xs text-viper-text/80 leading-relaxed">{event.message}</div>
      )}
      {m && (
        <div className="mt-2 flex gap-3 text-[11px] text-viper-muted">
          <span><b className="text-viper-text">{m.num_waypoints}</b> waypoints</span>
          <span><b className="text-viper-text">{Math.round(m.length_px)}</b> px</span>
          {m.min_clearance != null && (
            <span>clearance <b className="text-viper-text">{Math.round(m.min_clearance)}</b></span>
          )}
        </div>
      )}
    </div>
  );
}
