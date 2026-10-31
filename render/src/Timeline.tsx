import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * Data-driven vertical composition. A render spec (written by
 * src/dgl/render/build.py from a concept storyboard) lists scenes, captions
 * and audio cues; each scene picks a layout so different concepts can look
 * structurally different instead of sharing one template.
 */
export type Layout = 'bigText' | 'clock' | 'card' | 'split' | 'reveal';
export type Motion = 'none' | 'punchIn' | 'shake' | 'slideUp' | 'drift';

export type Scene = {
  start: number; // seconds
  end: number;
  layout: Layout;
  motion?: Motion;
  bg: string; // css color or gradient
  fg?: string;
  accent?: string;
  kicker?: string; // small label above the main text
  main: string;
  sub?: string;
  progress?: number; // 0..1 optional progress bar
};

export type Caption = {start: number; end: number; text: string};
export type AudioCue = {src: string; start: number; volume?: number};

export type Spec = {
  fps: number;
  duration: number;
  scenes: Scene[];
  captions: Caption[];
  audio: AudioCue[];
  safeTop?: number; // px kept clear for platform UI
  safeBottom?: number;
};

const FONT = '"Noto Sans CJK KR", "Noto Sans KR", "Apple SD Gothic Neo", sans-serif';

const useMotion = (motion: Motion | undefined, local: number, fps: number) => {
  const enter = spring({frame: local, fps, config: {damping: 14, stiffness: 160}});
  switch (motion) {
    case 'punchIn':
      return {transform: `scale(${interpolate(enter, [0, 1], [1.35, 1])})`, opacity: enter};
    case 'shake': {
      const amp = interpolate(local, [0, fps * 0.6], [22, 3], {extrapolateRight: 'clamp'});
      return {transform: `translate(${Math.sin(local * 2.7) * amp}px, ${Math.cos(local * 3.1) * amp * 0.6}px)`};
    }
    case 'slideUp':
      return {transform: `translateY(${interpolate(enter, [0, 1], [180, 0])}px)`, opacity: enter};
    case 'drift':
      return {transform: `scale(${1 + local / (fps * 40)})`};
    default:
      return {};
  }
};

const SceneView: React.FC<{scene: Scene; safeTop: number; safeBottom: number}> = ({scene, safeTop, safeBottom}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const style = useMotion(scene.motion, frame, fps);
  const fg = scene.fg ?? '#ffffff';
  const accent = scene.accent ?? '#ffd400';
  const base: React.CSSProperties = {
    fontFamily: FONT, color: fg, display: 'flex', flexDirection: 'column', justifyContent: 'center',
    alignItems: 'center', textAlign: 'center', padding: `${safeTop}px 90px ${safeBottom}px`,
  };
  const kicker = scene.kicker ? (
    <div style={{fontSize: 54, fontWeight: 700, color: accent, letterSpacing: 2, marginBottom: 28}}>{scene.kicker}</div>
  ) : null;
  const sub = scene.sub ? (
    <div style={{fontSize: 58, fontWeight: 500, opacity: 0.92, marginTop: 36, lineHeight: 1.35, whiteSpace: 'pre-line'}}>
      {scene.sub}
    </div>
  ) : null;

  let body: React.ReactNode;
  if (scene.layout === 'clock') {
    // a ticking clock: blinking colon, digits pop on scene entry
    const blink = Math.floor(frame / (fps / 2)) % 2 === 0 ? 1 : 0.25;
    const [h, m] = scene.main.split(':');
    body = (
      <>
        {kicker}
        <div style={{fontSize: 300, fontWeight: 900, fontVariantNumeric: 'tabular-nums', lineHeight: 1, ...style}}>
          {h}
          <span style={{opacity: blink}}>:</span>
          {m}
        </div>
        {sub}
      </>
    );
  } else if (scene.layout === 'card') {
    body = (
      <div style={{background: 'rgba(255,255,255,0.96)', color: '#111', borderRadius: 48, padding: '80px 70px',
        boxShadow: '0 30px 80px rgba(0,0,0,0.35)', width: '100%', ...style}}>
        {scene.kicker ? <div style={{fontSize: 46, fontWeight: 700, color: accent, marginBottom: 24}}>{scene.kicker}</div> : null}
        <div style={{fontSize: 92, fontWeight: 900, lineHeight: 1.2, whiteSpace: 'pre-line'}}>{scene.main}</div>
        {scene.sub ? <div style={{fontSize: 52, marginTop: 30, color: '#444', whiteSpace: 'pre-line'}}>{scene.sub}</div> : null}
      </div>
    );
  } else if (scene.layout === 'split') {
    const [top, bottom] = scene.main.split('|');
    body = (
      <div style={{width: '100%', ...style}}>
        <div style={{fontSize: 96, fontWeight: 900, paddingBottom: 60, borderBottom: `8px solid ${accent}`}}>{top}</div>
        <div style={{fontSize: 96, fontWeight: 900, paddingTop: 60, color: accent}}>{bottom}</div>
        {sub}
      </div>
    );
  } else if (scene.layout === 'reveal') {
    // text revealed left-to-right like a wipe
    const p = interpolate(frame, [0, fps * 0.8], [0, 100], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
    body = (
      <>
        {kicker}
        <div style={{fontSize: 120, fontWeight: 900, lineHeight: 1.15, whiteSpace: 'pre-line',
          clipPath: `inset(0 ${100 - p}% 0 0)`, ...style}}>{scene.main}</div>
        {sub}
      </>
    );
  } else {
    body = (
      <>
        {kicker}
        <div style={{fontSize: 128, fontWeight: 900, lineHeight: 1.15, whiteSpace: 'pre-line', ...style}}>{scene.main}</div>
        {sub}
      </>
    );
  }
  return (
    <AbsoluteFill style={{background: scene.bg}}>
      <AbsoluteFill style={base}>{body}</AbsoluteFill>
      {scene.progress !== undefined ? (
        <div style={{position: 'absolute', left: 0, top: 0, height: 18, width: `${scene.progress * 100}%`, background: accent}} />
      ) : null}
    </AbsoluteFill>
  );
};

const CaptionView: React.FC<{text: string; safeBottom: number}> = ({text, safeBottom}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 18, stiffness: 220}});
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: safeBottom + 40}}>
      <div style={{fontFamily: FONT, fontSize: 60, fontWeight: 800, color: '#fff', background: 'rgba(0,0,0,0.72)',
        padding: '18px 34px', borderRadius: 22, maxWidth: 940, textAlign: 'center', lineHeight: 1.3,
        transform: `scale(${interpolate(pop, [0, 1], [0.9, 1])})`}}>
        {text}
      </div>
    </AbsoluteFill>
  );
};

export const Timeline: React.FC<Spec> = ({fps, scenes, captions, audio, safeTop = 220, safeBottom = 380}) => {
  const f = (s: number) => Math.round(s * fps);
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {scenes.map((s, i) => (
        <Sequence key={`s${i}`} from={f(s.start)} durationInFrames={Math.max(1, f(s.end) - f(s.start))}>
          <SceneView scene={s} safeTop={safeTop} safeBottom={safeBottom} />
        </Sequence>
      ))}
      {captions.map((c, i) => (
        <Sequence key={`c${i}`} from={f(c.start)} durationInFrames={Math.max(1, f(c.end) - f(c.start))}>
          <CaptionView text={c.text} safeBottom={safeBottom} />
        </Sequence>
      ))}
      {audio.map((a, i) => (
        <Sequence key={`a${i}`} from={f(a.start)}>
          <Audio src={staticFile(a.src)} volume={a.volume ?? 1} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
