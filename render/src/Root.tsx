import React from 'react';
import {Composition} from 'remotion';
import {Spec, Timeline} from './Timeline';

const preview: Spec = {
  fps: 30,
  duration: 3,
  scenes: [{start: 0, end: 3, layout: 'bigText', motion: 'punchIn', bg: '#111', main: '미리보기'}],
  captions: [],
  audio: [],
};

export const Root: React.FC = () => (
  <Composition
    id="Timeline"
    component={Timeline}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={90}
    defaultProps={preview}
    calculateMetadata={({props}) => ({
      fps: props.fps,
      durationInFrames: Math.round(props.duration * props.fps),
    })}
  />
);
