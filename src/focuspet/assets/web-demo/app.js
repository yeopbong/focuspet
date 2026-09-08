'use strict';
const $ = id => document.getElementById(id), data = window.FOCUS_PET_DEMO;
let scenario = Object.keys(data.scenarios)[0], branch = 'original', position = 0;
let playing = false, character = 'mira', frameIndex = 0;
const colors = {Focused:'#699271', Normal:'#bac8a8', Distracted:'#d2b392', Rest:'#afc5ca', Unknown:'#dfe1dc'};
const images = {};
for (const [id, manifest] of Object.entries(data.characters)) {
  const image = new Image();
  image.src = `characters/${id}/sprites.png`;
  images[id] = image;
  const button = document.createElement('button');
  button.title = manifest.name;
  button.setAttribute('aria-label', `Choose ${manifest.name}`);
  button.innerHTML = `<img src="characters/${id}/thumbnail.png" alt="">`;
  button.onclick = () => { character = id; frameIndex = 0; render(); };
  button.dataset.character = id;
  $('characters').appendChild(button);
}
for (const [id, item] of Object.entries(data.scenarios)) {
  const option = document.createElement('option');
  option.value = id;
  option.textContent = item.title;
  $('scenario').appendChild(option);
}
const trajectory = () => data.scenarios[scenario][branch] || data.scenarios[scenario].original;
const rows = () => trajectory().snapshots;
function text(id, value) { $(id).textContent = value; }
function clock(seconds) {
  return `${String(Math.floor(seconds/3600)).padStart(2,'0')}:${String(Math.floor(seconds/60)%60).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;
}
function setPlaying(value) {
  playing = value;
  text('play', playing ? 'Ⅱ' : '▶');
  $('play').setAttribute('aria-label', playing ? 'Pause playback' : 'Play playback');
  $('play').setAttribute('aria-pressed', String(playing));
}
function renderTimeline() {
  $('strip').replaceChildren();
  rows().forEach((row, index) => {
    const segment = document.createElement('span');
    segment.style.setProperty('--c', colors[row.state] || colors.Unknown);
    segment.title = `${clock(row.elapsed_s)} · ${row.state}`;
    segment.dataset.state = row.state;
    segment.onclick = () => { position = index; render(); };
    $('strip').appendChild(segment);
  });
}
function renderPlot() {
  const history = rows(), parts = [];
  const ymax = Math.max(130, ...history.map(row => row.workload));
  const X = index => 20 + index / Math.max(1, history.length-1) * 960;
  const Y = value => 175 - (value+10) / (ymax+10) * 155;
  for (const value of [0, 40, 80, 100, 120]) {
    parts.push(`<line x1="20" y1="${Y(value)}" x2="980" y2="${Y(value)}" stroke="${value===100?'#c1ba9e':'#e5e9df'}" stroke-dasharray="${value===100?'5 5':'0'}"/><text x="3" y="${Y(value)-4}" fill="#909b87" font-size="9">${value}</text>`);
  }
  for (const [field, color, dash] of [['workload','#64835e',''], ['focus','#c3cdae','4 4']]) {
    let current = [];
    function flush() {
      if (current.length) parts.push(`<polyline points="${current.join(' ')}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-dasharray="${dash}"/>`);
      current = [];
    }
    history.forEach((row, index) => {
      if (row[field] === null || (field === 'workload' && row.workload_stale)) { flush(); return; }
      current.push(`${X(index)},${Y(row[field])}`);
    });
    flush();
  }
  parts.push(`<line x1="${X(position)}" x2="${X(position)}" y1="10" y2="178" stroke="#7f9270" stroke-dasharray="3 3"/>`);
  $('plot').innerHTML = parts.join('');
}
function render() {
  const history = rows();
  position = Math.max(0, Math.min(position, history.length-1));
  const row = history[position];
  if (!row) return;
  const prediction = row.prediction;
  text('state', row.state);
  text('observation', row.observation);
  text('source', prediction.source);
  text('focus', row.focus === null ? '—' : Math.round(row.focus));
  text('load', row.workload > 120 ? '120+' : Math.round(row.workload));
  text('load-detail', row.workload_stale ? 'Last value · observation gap' : `Continuous value ${row.workload.toFixed(1)} · reference 100`);
  text('evidence', prediction.reason.join(' '));
  text('coverage', `${Math.round(prediction.coverage*100)}%`);
  text('uncertainty', prediction.uncertainty.toFixed(2));
  text('clock', clock(row.elapsed_s));
  text('speech', row.state === 'Rest' ? 'A little space to recharge.' : row.state === 'Unknown' ? 'There is room for uncertainty.' : 'Let’s take this one moment at a time.');
  text('character-name', data.characters[character].name);
  text('character-tagline', data.characters[character].tagline);
  $('seek').max = history.length-1;
  $('seek').value = position;
  document.querySelectorAll('[data-character]').forEach(button => {
    const active = button.dataset.character === character;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  text('branch-note', branch === 'corrected'
    ? 'Precomputed re-evaluation: a synthetic user declares Normal for 5 minutes. No browser training occurs; original history remains available.'
    : 'Playing exported estimates. Explore the precomputed correction branch; desktop training is a separate feature.');
  text('correct', branch === 'original' ? 'Explore a correction' : 'Return to original');
  $('correct').setAttribute('aria-pressed', String(branch === 'corrected'));
  renderPlot();
}
function reset() {
  branch = 'original';
  position = 0;
  frameIndex = 0;
  setPlaying(false);
  renderTimeline();
  render();
}
$('scenario').onchange = event => { scenario = event.target.value; reset(); };
$('seek').oninput = event => { position = Number(event.target.value); render(); };
$('play').onclick = () => {
  if (!playing && position === rows().length-1) position = 0;
  setPlaying(!playing);
  render();
};
$('reset').onclick = reset;
$('correct').onclick = () => {
  setPlaying(false);
  branch = branch === 'original' ? 'corrected' : 'original';
  const target = data.scenarios[scenario].corrected.correction?.at_elapsed_s ?? 3000;
  position = Math.max(0, rows().findIndex(row => row.elapsed_s >= target+60));
  renderTimeline();
  render();
};
$('jump-rest').onclick = () => {
  const index = rows().findIndex((row, i) => i > position && row.state === 'Rest');
  position = index >= 0 ? index : Math.max(0, rows().findIndex(row => row.state === 'Rest'));
  render();
};
setInterval(() => {
  if (!playing) return;
  position += Number($('speed').value);
  if (position >= rows().length-1) { position = rows().length-1; setPlaying(false); }
  render();
}, 250);
setInterval(() => {
  const row = rows()[position], manifest = data.characters[character];
  let action = {Focused:'focus', Normal:'normal', Distracted:'distracted', Rest:'rest', Unknown:'unknown'}[row?.state] || 'idle';
  if (row?.workload >= 80 && row.state !== 'Rest' && row.state !== 'Unknown') action = 'stretch';
  const frames = (manifest.actions[action] || manifest.actions.idle).frames;
  const frame = frames[frameIndex++ % frames.length], context = $('character').getContext('2d');
  context.clearRect(0, 0, 64, 80);
  context.imageSmoothingEnabled = false;
  const image = images[character];
  if (image.complete && image.naturalWidth) context.drawImage(image, (frame%4)*64, Math.floor(frame/4)*80, 64, 80, 0, 0, 64, 80);
}, 400);
reset();
