/**
 * Pixel-pet engine - a framework-agnostic port of LucasHJin/obsidian-pets
 * (MIT, itself based on tonybaloney/vscode-pets). Sprite-sheet characters that
 * walk, idle, jump, sleep and play inside any container element. Obsidian-only
 * APIs (createDiv / setCssProps / activeWindow / bundler PNG imports) have been
 * swapped for plain DOM + public asset URLs so it runs anywhere in the browser.
 *
 * Used to keep people company on Zivo's loading screens (the zero-perceived-wait
 * north star). Drive it through the <PetPlayground> React wrapper, not directly.
 */

/* ----------------------------- assets ----------------------------- */

const ASSET_BASE = "/pet-assets";

export function petAsset(type: string, file: string): string {
  // `type` is e.g. "pets/brown-cat"; mirrors the upstream petAssets keys.
  return `${ASSET_BASE}/${type}/${file}`;
}
export function toyAsset(name: string): string {
  return `${ASSET_BASE}/toys/${name}.png`;
}
const HEART_URL = `${ASSET_BASE}/misc/heart.png`;

/* ----------------------------- sounds ----------------------------- */

const SOUND_URLS: Record<string, string> = {
  cat: `${ASSET_BASE}/sounds/cat.mp3`,
  bunny: `${ASSET_BASE}/sounds/bunny.mp3`,
  ghost: `${ASSET_BASE}/sounds/ghost.mp3`,
  spawn: `${ASSET_BASE}/sounds/spawn.mp3`,
  bounce: `${ASSET_BASE}/sounds/bounce.mp3`,
};
const BALL_MAX_SPEED = 10;
let soundEnabled = false;

export function setSoundEnabled(enabled: boolean): void {
  soundEnabled = enabled;
}

function playPetSound(sound: string): void {
  if (!soundEnabled) return;
  try {
    const audio = new Audio(SOUND_URLS[sound]);
    audio.volume = 0.3 + Math.random() * 0.1;
    audio.preservesPitch = false;
    audio.playbackRate = 0.85 + Math.random() * 0.4;
    void audio.play();
  } catch {
    /* autoplay may be blocked; ignore */
  }
}

function playBallSound(speed: number): void {
  if (!soundEnabled) return;
  try {
    const audio = new Audio(SOUND_URLS.bounce);
    const t = Math.min(speed / BALL_MAX_SPEED, 1);
    audio.volume = 0.5 + t * 0.3;
    audio.preservesPitch = false;
    audio.playbackRate = 1.3 - t * 0.3;
    void audio.play();
  } catch {
    /* ignore */
  }
}

/* --------------------------- DOM helpers --------------------------- */

function makeDiv(parent: Element, cls: string): HTMLElement {
  const el = document.createElement("div");
  el.className = cls;
  parent.appendChild(el);
  return el;
}
function setVars(el: HTMLElement, props: Record<string, string>): void {
  for (const [k, v] of Object.entries(props)) el.style.setProperty(k, v);
}

/* --------------------------- animations ---------------------------- */

export type AnimationConfig = {
  name: string;
  spriteUrl: string;
  frameCount: number;
  frameWidth: number;
  frameHeight: number;
  duration: number;
  action?: (multiples?: number) => Promise<void> | void;
};

type PetAnimations = Record<string, AnimationConfig>;

function alterDuration(base: number, variation = 100): number {
  return base + (Math.floor(Math.random() * (variation * 2 + 1)) - variation);
}

function getCatAnimations(type: string): PetAnimations {
  const a: PetAnimations = {
    idle: { name: "idle", spriteUrl: petAsset(type, "idle-cat.png"), frameCount: 7, frameWidth: 32, frameHeight: 32, duration: alterDuration(700) },
    jump: { name: "jump", spriteUrl: petAsset(type, "jump-cat.png"), frameCount: 13, frameWidth: 32, frameHeight: 32, duration: alterDuration(1300) },
    jump2: { name: "jump2", spriteUrl: petAsset(type, "jump2-cat.png"), frameCount: 9, frameWidth: 32, frameHeight: 32, duration: alterDuration(900) },
    run: { name: "run", spriteUrl: petAsset(type, "run-cat.png"), frameCount: 7, frameWidth: 32, frameHeight: 32, duration: alterDuration(700) },
    sit: { name: "sit", spriteUrl: petAsset(type, "sitting-cat.png"), frameCount: 3, frameWidth: 32, frameHeight: 32, duration: alterDuration(750) },
    sleep: { name: "sleep", spriteUrl: petAsset(type, "sleep-cat.png"), frameCount: 3, frameWidth: 32, frameHeight: 32, duration: alterDuration(750) },
    die: { name: "die", spriteUrl: petAsset(type, "die-cat.png"), frameCount: 15, frameWidth: 32, frameHeight: 32, duration: alterDuration(1500) },
  };
  if (type !== "pets/batman-black-cat" && type !== "pets/batman-blue-cat") {
    a.idle2 = { name: "idle2", spriteUrl: petAsset(type, "idle2-cat.png"), frameCount: 14, frameWidth: 32, frameHeight: 32, duration: alterDuration(1400) };
  }
  if (type === "pets/witch-cat") {
    delete a.jump;
    delete a.jump2;
    a.fly = { name: "fly", spriteUrl: petAsset(type, "fly-cat.png"), frameCount: 3, frameWidth: 32, frameHeight: 32, duration: alterDuration(600) };
  } else if (type === "pets/classic-cat") {
    a.liking = { name: "liking", spriteUrl: petAsset(type, "liking-cat.png"), frameCount: 18, frameWidth: 32, frameHeight: 32, duration: alterDuration(1800) };
  }
  return a;
}

function getBunnyAnimations(type: string): PetAnimations {
  return {
    idle: { name: "idle", spriteUrl: petAsset(type, "idle-bunny.png"), frameCount: 12, frameWidth: 32, frameHeight: 32, duration: alterDuration(1200, 150) },
    idle2: { name: "liedown", spriteUrl: petAsset(type, "liedown-bunny.png"), frameCount: 6, frameWidth: 32, frameHeight: 32, duration: alterDuration(600, 150) },
    jump: { name: "jump", spriteUrl: petAsset(type, "jump-bunny.png"), frameCount: 11, frameWidth: 32, frameHeight: 32, duration: alterDuration(1100, 150) },
    run: { name: "run", spriteUrl: petAsset(type, "run-bunny.png"), frameCount: 8, frameWidth: 32, frameHeight: 32, duration: alterDuration(800, 150) },
    sit: { name: "like", spriteUrl: petAsset(type, "like-bunny.png"), frameCount: 5, frameWidth: 32, frameHeight: 32, duration: alterDuration(500, 150) },
    sleep: { name: "sleep", spriteUrl: petAsset(type, "sleep-bunny.png"), frameCount: 6, frameWidth: 32, frameHeight: 32, duration: alterDuration(600, 150) },
    die: { name: "die", spriteUrl: petAsset(type, "die-bunny.png"), frameCount: 12, frameWidth: 32, frameHeight: 32, duration: alterDuration(1200, 150) },
  };
}

function getGhostAnimations(type: string): PetAnimations {
  return {
    idle: { name: "idle", spriteUrl: petAsset(type, "idle-ghost.png"), frameCount: 8, frameWidth: 32, frameHeight: 32, duration: alterDuration(1200, 150) },
  };
}

/* ------------------------------ Ball ------------------------------- */

class Ball {
  private container: Element;
  ballEl: HTMLElement;
  private x: number;
  private y: number;
  private vx: number;
  private vy: number;
  private radius: number;
  private gravity = 0.42;
  private damping = 0.99;
  private airRes = 0.99;
  private frameId: number | null = null;
  private destroyed = false;
  onDestroy?: () => void;

  constructor(container: Element, spriteUrl: string, scale: number, groundFraction: number) {
    this.container = container;
    this.groundFraction = groundFraction;
    const BALL_SIZE = 10;
    // Visual radius: the element is CSS-scaled, so collisions must use the
    // scaled size or the ball clips through walls/floor.
    this.radius = (BALL_SIZE / 2) * scale;

    this.ballEl = makeDiv(container, "zv-pet-ball");
    const img = document.createElement("img");
    img.src = spriteUrl;
    this.ballEl.appendChild(img);
    setVars(this.ballEl, {
      "--ballwidth": `${BALL_SIZE}px`,
      "--ballheight": `${BALL_SIZE}px`,
      "--scale": `${scale}`,
    });

    const rect = container.getBoundingClientRect();
    this.x = Math.random() * (rect.width - this.radius * 2) + this.radius;
    this.y = Math.random() * (rect.height * 0.15 - this.radius * 2) + this.radius;
    this.vx = (Math.random() - 0.5) * 15;
    this.vy = (Math.random() - 0.5) * 8;
    // Position before first paint - otherwise the ball flashes at (0,0).
    setVars(this.ballEl, { "--x": `${this.x - 5}px`, "--y": `${this.y - 5}px` });

    this.update = this.update.bind(this);
    this.frameId = requestAnimationFrame(this.update);
    window.setTimeout(() => this.destroy(), 5000);
  }

  private groundFraction: number;

  private getGroundHeight(): number {
    // Same ground line the pets' feet stand on.
    const rect = this.container.getBoundingClientRect();
    return rect.height * this.groundFraction;
  }

  private update(): void {
    if (this.destroyed) return;
    const rect = this.container.getBoundingClientRect();
    this.vy += this.gravity;
    this.x += this.vx;
    this.y += this.vy;
    if (this.x - this.radius < 0) {
      this.x = this.radius;
      playBallSound(Math.abs(this.vx));
      this.vx *= -this.damping;
    } else if (this.x + this.radius > rect.width) {
      this.x = rect.width - this.radius;
      playBallSound(Math.abs(this.vx));
      this.vx *= -this.damping;
    }
    const ground = this.getGroundHeight();
    if (this.y - this.radius < 0) {
      this.y = this.radius;
      this.vy *= -this.damping;
    } else if (this.y + this.radius > ground) {
      this.y = ground - this.radius;
      playBallSound(Math.abs(this.vy));
      this.vy *= -this.damping;
    }
    this.vx *= this.airRes;
    this.vy *= this.airRes;
    // Offset by the *unscaled* half-size (5px): CSS scale() runs around the
    // element center, so the center stays where translate() put it.
    setVars(this.ballEl, { "--x": `${this.x - 5}px`, "--y": `${this.y - 5}px` });
    this.frameId = requestAnimationFrame(this.update);
  }

  getPosition(): { x: number; y: number } {
    return { x: this.x, y: this.y };
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.frameId) cancelAnimationFrame(this.frameId);
    this.ballEl.remove();
    this.onDestroy?.();
  }
}

/* ------------------------------ Pet -------------------------------- */

class Pet {
  protected container: Element;
  petEl!: HTMLElement;
  protected currentX = 0;
  protected currentY = 0;
  protected direction = 1;
  private currentAnimation = "none";
  protected animations: PetAnimations;
  protected isDestroyed = false;
  protected moveDist: number;
  protected scale: number;
  protected petName: string;
  protected groundFraction: number;
  // When true the pet roams the whole 2D area (varying its top as well as its
  // left) instead of walking one horizontal ground line.
  protected wander: boolean;
  protected tooltipEl!: HTMLElement;
  protected actionLoopPaused = false;
  private interruptMove: (() => void) | null = null;
  protected soundKey = "cat";

  constructor(container: Element, animations: PetAnimations, moveDist: number, scale: number, petName: string, groundFraction: number, wander = false) {
    this.container = container;
    this.animations = animations;
    this.moveDist = moveDist;
    this.scale = scale;
    this.petName = petName;
    this.groundFraction = groundFraction;
    this.wander = wander;

    this.setupActions();

    // Defer one tick so the container has laid out (offsetWidth is real).
    // setTimeout rather than rAF so spawning still happens in a backgrounded
    // tab, where rAF is paused.
    window.setTimeout(() => {
      if (this.isDestroyed) return;
      const el = this.container as HTMLElement;
      const w = el.offsetWidth;
      const halfW = (this.animations["idle"].frameWidth * this.scale) / 2;
      this.currentX = halfW + Math.random() * Math.max(w - halfW * 2, 0);
      if (this.wander) {
        const halfH = (this.animations["idle"].frameHeight * this.scale) / 2;
        this.currentY = halfH + Math.random() * Math.max(el.offsetHeight - halfH * 2, 0);
      }
      this.petEl = this.createPetElement();
      this.setupHoverListeners();
      void (async () => {
        await this.animations["idle"].action?.();
        this.startActionLoop();
      })();
    });
  }

  protected createPetElement(): HTMLElement {
    const el = makeDiv(this.container, this.wander ? "zv-pet zv-pet--wander" : "zv-pet");
    // The element is centered on (--left, --top) then scaled, so put the feet
    // line (drawn at row 31 of the 32px frame) exactly on the ground line -
    // otherwise the scaled bottom half hangs below it and gets clipped by the
    // container's overflow:hidden. When wandering there is no ground line, so
    // the pet floats freely at its current 2D position.
    const feetOffset = (this.animations["idle"].frameHeight / 2 - 1) * this.scale;
    setVars(el, {
      "--left": `${this.currentX}px`,
      "--top": this.wander ? `${this.currentY}px` : `calc(${this.groundFraction * 100}% - ${feetOffset}px)`,
      "--pet-size": `${this.animations["idle"].frameWidth}px`,
      "--scale-x": `${this.direction}`,
      "--scale": `${this.scale}`,
      "--heart-url": `url(${HEART_URL})`,
    });
    // Soft ground shadow that travels with the pet - grounds it so it reads
    // as standing on a surface rather than floating.
    makeDiv(el, "zv-pet-shadow");
    this.tooltipEl = makeDiv(el, "zv-pet-name-tooltip");
    this.tooltipEl.textContent = this.petName;
    setVars(this.tooltipEl, { "--scale-x": `${this.direction}` });
    return el;
  }

  protected setupHoverListeners(): void {
    this.petEl.addEventListener("mouseenter", () => {
      this.actionLoopPaused = true;
      this.freezeAtCurrentPosition();
      this.setAnimation(this.animations["sit"] ? "sit" : "idle");
    });
    this.petEl.addEventListener("mouseleave", () => {
      this.actionLoopPaused = false;
    });
    this.petEl.addEventListener("click", (e) => {
      e.stopPropagation();
      this.showHeart();
      playPetSound(this.soundKey);
    });
  }

  protected showHeart(): void {
    const heart = makeDiv(this.petEl, "zv-pet-heart");
    setVars(heart, { "--heart-random-x": `${25 + Math.random() * 50}%` });
    window.setTimeout(() => heart.remove(), 1000);
  }

  protected setupActions(): void {}

  protected setAnimation(name: string): void {
    if (this.currentAnimation === name) return;
    const animation = this.animations[name];
    if (!animation) return;
    this.petEl.style.animation = "none";
    void this.petEl.offsetHeight; // reflow
    this.petEl.style.animation = `zv-pet-sprite ${animation.duration}ms steps(${animation.frameCount}) infinite`;
    setVars(this.petEl, {
      "--sprite-url": `url(${animation.spriteUrl})`,
      "--sprite-size": `${animation.frameCount * animation.frameWidth}px auto`,
      "--sprite-total-width": `-${animation.frameCount * animation.frameWidth}px`,
    });
    this.currentAnimation = name;
  }

  protected freezeAtCurrentPosition(): void {
    const computedLeft = window.getComputedStyle(this.petEl).left;
    this.petEl.style.transition = "none";
    setVars(this.petEl, { "--left": computedLeft });
    void this.petEl.offsetHeight;
    this.petEl.style.transition = "";
    this.currentX = parseFloat(computedLeft);
    this.interruptMove?.();
  }

  protected move(duration: number, action?: string): Promise<void> {
    if (this.actionLoopPaused || this.isDestroyed) return Promise.resolve();
    // Clamp with the *scaled* half-width so the sprite never pokes past the edges.
    const halfW = (this.animations["idle"].frameWidth * this.scale) / 2;
    const containerWidth = (this.container as HTMLElement).offsetWidth;
    const maxLeft = containerWidth - halfW;
    const minLeft = halfW;
    const magnitude = action?.includes("jump") ? this.moveDist * (Math.random() * 0.3 + 1.5) : this.moveDist;
    const direction = Math.random() < 0.9 ? this.direction : -this.direction;
    let dx = magnitude * direction;
    const possibleX = this.currentX + dx;
    if (possibleX < minLeft || possibleX > maxLeft) dx = -dx;
    const targetX = this.currentX + dx;
    if (targetX === this.currentX) return Promise.resolve();
    this.direction = dx < 0 ? -1 : 1;

    // 2D roaming: drift toward a new vertical spot each move so the pet explores
    // the whole free area rather than a single horizontal line.
    let targetY = this.currentY;
    if (this.wander) {
      const halfH = (this.animations["idle"].frameHeight * this.scale) / 2;
      const containerHeight = (this.container as HTMLElement).offsetHeight;
      const dy = (Math.random() - 0.5) * this.moveDist * 1.6;
      targetY = Math.max(halfH, Math.min(containerHeight - halfH, this.currentY + dy));
      this.currentY = targetY;
    }

    return new Promise((res) => {
      let settled = false;
      let timer: number | undefined;
      const settle = () => {
        if (settled) return;
        settled = true;
        if (timer) window.clearTimeout(timer);
        this.petEl.removeEventListener("transitionend", done);
        this.interruptMove = null;
        res();
      };
      const done = (e?: TransitionEvent) => {
        if (e && (e.target !== this.petEl || e.propertyName !== "left")) return;
        this.petEl.style.transition = "";
        this.currentX = targetX;
        settle();
      };
      this.petEl.addEventListener("transitionend", done);
      // Safety net: `transitionend` doesn't fire in backgrounded tabs (and can be
      // dropped if the transition is interrupted), which would stall the whole
      // behaviour loop. Always settle shortly after the expected duration.
      timer = window.setTimeout(() => done(), duration + 80);
      this.interruptMove = settle;
      void this.petEl.offsetWidth;
      setVars(this.petEl, {
        "--left": `${targetX}px`,
        ...(this.wander ? { "--top": `${targetY}px` } : {}),
        "--scale-x": `${this.direction}`,
        "--move-duration": `${duration}ms`,
      });
      setVars(this.tooltipEl, { "--scale-x": `${this.direction}` });
    });
  }

  protected async startActionLoop(): Promise<void> {
    const getRandDelay = (min: number, max: number, multiple: number) =>
      Math.round((Math.random() * (max - min) + min) / multiple) * multiple;
    const ACTIONS = Object.keys(this.animations).filter((a) => a !== "die" && a !== "run");
    while (!this.isDestroyed) {
      while (this.actionLoopPaused && !this.isDestroyed) await wait(100);
      if (this.isDestroyed) break;
      const randomAction = ACTIONS[Math.floor(Math.random() * ACTIONS.length)];
      await this.animations[randomAction].action?.();
      while (this.actionLoopPaused && !this.isDestroyed) await wait(100);
      if (this.isDestroyed) break;
      const delay = getRandDelay(3000, 8000, this.animations["run"].duration);
      await this.animations["run"].action?.(Math.floor(delay / this.animations["run"].duration));
    }
  }

  destroyImmediate(): void {
    this.isDestroyed = true;
    this.interruptMove?.();
    this.petEl?.remove();
  }
}

/* ------------------------------ Cat -------------------------------- */

class Cat extends Pet {
  private canFly: boolean;
  private chasingBall: Ball | null = null;
  private interruptAction = false;

  constructor(container: Element, animations: PetAnimations, moveDist: number, scale: number, petName: string, groundFraction: number, canFly = false, wander = false) {
    super(container, animations, moveDist, scale, petName, groundFraction, wander);
    this.soundKey = "cat";
    this.canFly = canFly;
  }

  protected setupActions(): void {
    for (const key in this.animations) {
      this.animations[key].action = async (multiples = 1) => {
        this.setAnimation(key);
        if (key === "run" || key === "fly") {
          for (let i = 0; i < multiples; i++) {
            if (this.interruptAction) break;
            await this.move(this.animations[key].duration, key);
          }
        } else if (key === "jump" || key === "jump2") {
          if (!this.interruptAction) await this.move(this.animations[key].duration, key);
        } else if (key === "sit" || key === "sleep") {
          const n = Math.floor(Math.random() * 3) + 2;
          for (let i = 0; i < n; i++) {
            if (this.interruptAction) break;
            await wait(this.animations[key].duration);
          }
        } else {
          const n = Math.floor(Math.random() * 2) + 2;
          for (let i = 0; i < n; i++) {
            if (this.interruptAction) break;
            await wait(this.animations[key].duration);
          }
        }
      };
    }
  }

  startChasingBall(ball: Ball): void {
    this.chasingBall = ball;
    this.interruptAction = true;
  }
  stopChasingBall(): void {
    this.chasingBall = null;
    this.interruptAction = false;
  }

  protected async startActionLoop(): Promise<void> {
    const getRandDelay = (min: number, max: number, multiple: number) =>
      Math.round((Math.random() * (max - min) + min) / multiple) * multiple;
    // Weighted behaviour pool: mostly brief idles and the odd jump, with sit /
    // sleep kept rare so the pets spend most of their time walking around.
    const ACTIONS: string[] = [];
    for (const a of Object.keys(this.animations)) {
      if (a === "die" || a === "run" || a === "fly") continue;
      if (a === "idle" || a === "idle2") ACTIONS.push(a, a, a);
      else if (a === "jump" || a === "jump2") ACTIONS.push(a, a);
      else ACTIONS.push(a); // sit / sleep / liking - rare
    }
    while (!this.isDestroyed) {
      if (this.chasingBall) {
        this.interruptAction = false;
        while (this.chasingBall && !this.isDestroyed) {
          await this.chaseBall();
          await wait(100);
        }
        this.freezeAtCurrentPosition();
        this.setAnimation("idle");
      } else {
        while (this.actionLoopPaused && !this.isDestroyed) await wait(100);
        if (this.isDestroyed) break;
        const randomAction = ACTIONS[Math.floor(Math.random() * ACTIONS.length)];
        await this.animations[randomAction].action?.();
        while (this.actionLoopPaused && !this.isDestroyed) await wait(100);
        if (this.isDestroyed) break;
        const movingAnimation = this.canFly ? (Math.random() < 0.3 ? "fly" : "run") : "run";
        const delay = getRandDelay(3000, 8000, this.animations[movingAnimation].duration);
        await this.animations[movingAnimation].action?.(Math.floor(delay / this.animations[movingAnimation].duration));
      }
    }
  }

  private async chaseBall(): Promise<void> {
    if (!this.chasingBall || this.isDestroyed) return;
    const halfW = (this.animations["idle"].frameWidth * this.scale) / 2;
    const containerWidth = (this.container as HTMLElement).offsetWidth;
    const minLeft = halfW;
    const maxLeft = containerWidth - halfW;
    for (let i = 0; i < 3; i++) {
      if (!this.chasingBall) break;
      const dx = this.chasingBall.getPosition().x - this.currentX;
      this.direction = dx < 0 ? -1 : 1;
      const moveAmount = Math.sign(dx) * Math.min(Math.abs(dx), this.moveDist);
      let newX = this.currentX + moveAmount;
      if (newX < minLeft) newX = minLeft;
      if (newX > maxLeft) newX = maxLeft;
      this.currentX = newX;
      setVars(this.petEl, { "--left": `${this.currentX}px`, "--scale-x": `${this.direction}` });
      setVars(this.tooltipEl, { "--scale-x": `${this.direction}` });
      this.setAnimation("run");
      await wait(200);
      if (this.interruptAction) break;
    }
    this.setAnimation("idle");
  }
}

/* ------------------------------ Bunny ------------------------------ */

class Bunny extends Pet {
  protected setupActions(): void {
    this.soundKey = "bunny";
    for (const key in this.animations) {
      this.animations[key].action = async (multiples = 1) => {
        this.setAnimation(key);
        if (key === "run") {
          for (let i = 0; i < multiples; i++) await this.move(this.animations[key].duration, key);
        } else if (key === "jump") {
          await this.move(this.animations[key].duration, key);
        } else if (key === "like" || key === "sleep" || key === "liedown") {
          await wait(this.animations[key].duration * (Math.floor(Math.random() * 7) + 6));
        } else {
          await wait(this.animations[key].duration * (Math.floor(Math.random() * 2) + 2));
        }
      };
    }
  }
}

/* ------------------------------ Ghost ------------------------------ */

class Ghost extends Pet {
  protected setupActions(): void {
    this.soundKey = "ghost";
    this.animations["idle"].action = async () => {
      this.setAnimation("idle");
      await wait(this.animations["idle"].duration);
    };
  }

  protected async startActionLoop(): Promise<void> {
    while (!this.isDestroyed) {
      if (Math.random() < 0.9) {
        const repeats = 3 + Math.floor(Math.random() * 6);
        for (let i = 0; i < repeats; i++) await this.move(this.animations["idle"].duration, "idle");
      }
      await this.animations["idle"].action?.();
      await wait(2000 + Math.random() * 3000);
    }
  }
}

function wait(ms: number): Promise<void> {
  return new Promise((res) => window.setTimeout(res, ms));
}

function shuffle<T>(arr: T[]): T[] {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

/* --------------------------- pet roster ---------------------------- */

// Colorful, light-on-their-feet cats picked so the strip reads bright and
// varied on a pale loading screen - the near-black variants (black-cat,
// batman-black, demon, vampire, witch) are intentionally left out so no pet
// shows up as a dark blob. The full set still lives in /pet-assets if needed.
const CAT_TYPES = [
  "classic-cat", "brown-cat", "tiger-cat", "siamese-cat", "white-cat",
  "three-cat", "deer-cat", "egypt-cat", "xmas-cat", "xmas-v2-cat",
  "xmas-v3-cat", "pirate-cat",
];

const BALL_COLORS = ["blue", "cyan", "green", "orange", "pink", "purple", "red", "yellow"];

const PET_NAMES = [
  "Mochi", "Pixel", "Biscuit", "Noodle", "Sprout", "Tofu", "Pickle", "Waffle",
  "Sage", "Clover", "Pumpkin", "Mistletoe", "Boba", "Sushi", "Pesto", "Marble",
  "Ziggy", "Comet", "Pip", "Juniper",
];

export type Species = "cat" | "bunny" | "ghost" | "random";

/* --------------------------- PetWorld ------------------------------ */

export type PetWorldOptions = {
  count?: number;
  scale?: number;
  species?: Species;
  interactive?: boolean;
  sound?: boolean;
  /** Vertical ground line as a fraction of container height (0-1). */
  groundFraction?: number;
  /** Roam the whole 2D area instead of walking one horizontal ground line. */
  wander?: boolean;
};

// App-wide singleton: only ONE PetWorld may be alive at a time, so the user
// never sees pets from two overlapping mounts (e.g. a loading screen fading into
// the next). The newest mount wins - it disposes any predecessor.
let activeWorld: PetWorld | null = null;

/**
 * Owns a container element and the pets living in it. Spawns the roster, wires
 * click-to-throw-ball, and tears everything down on dispose(). One instance per
 * mounted <PetPlayground>.
 */
export class PetWorld {
  private container: HTMLElement;
  private pets: Pet[] = [];
  private cats: Cat[] = [];
  private balls: Ball[] = [];
  private opts: Required<PetWorldOptions>;
  private clickHandler?: (e: MouseEvent) => void;
  private disposed = false;

  constructor(container: HTMLElement, options: PetWorldOptions = {}) {
    // Enforce the one-pet-world-at-a-time rule before spawning anything.
    if (activeWorld && activeWorld !== this) activeWorld.dispose();
    activeWorld = this;
    this.container = container;
    this.opts = {
      count: options.count ?? 3,
      scale: options.scale ?? 2,
      species: options.species ?? "random",
      interactive: options.interactive ?? true,
      sound: options.sound ?? false,
      groundFraction: options.groundFraction ?? 0.82,
      wander: options.wander ?? false,
    };
    setSoundEnabled(this.opts.sound);
    this.spawn();
    if (this.opts.interactive) this.enableBallThrowing();
  }

  private spawn(): void {
    const { count, scale, species, groundFraction, wander } = this.opts;
    // Distinct cat colors: draw from a shuffled pool so no two cats match
    // (until the pool runs out), giving every pet its own look.
    const catPool = shuffle([...CAT_TYPES]);
    const names = shuffle([...PET_NAMES]);
    let catIdx = 0;
    for (let i = 0; i < count; i++) {
      const moveDist = 32 + Math.random() * 20;
      let kind: "cat" | "bunny" | "ghost" = "cat";
      if (species === "random") {
        // Mostly cats, with the occasional bunny for variety; ghost is rare.
        const r = Math.random();
        kind = r < 0.82 ? "cat" : r < 0.95 ? "bunny" : "ghost";
      } else {
        kind = species;
      }
      const name = names[i % names.length];
      if (kind === "bunny") {
        this.pets.push(new Bunny(this.container, getBunnyAnimations("pets/grey-bunny"), moveDist, scale, name, groundFraction, wander));
      } else if (kind === "ghost") {
        this.pets.push(new Ghost(this.container, getGhostAnimations("pets/ghost"), moveDist, scale, name, groundFraction, wander));
      } else {
        const type = catPool[catIdx++ % catPool.length];
        const cat = new Cat(this.container, getCatAnimations(`pets/${type}`), moveDist, scale, name, groundFraction, false, wander);
        this.cats.push(cat);
        this.pets.push(cat);
      }
    }
  }

  private enableBallThrowing(): void {
    this.clickHandler = () => {
      if (this.disposed) return;
      const color = BALL_COLORS[Math.floor(Math.random() * BALL_COLORS.length)];
      const ball = new Ball(this.container, toyAsset(`${color}-ball`), this.opts.scale, this.opts.groundFraction);
      ball.onDestroy = () => {
        this.balls = this.balls.filter((b) => b !== ball);
        this.cats.forEach((c) => c.stopChasingBall());
      };
      this.balls.push(ball);
      this.cats.forEach((c) => c.startChasingBall(ball));
    };
    this.container.addEventListener("click", this.clickHandler);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (activeWorld === this) activeWorld = null;
    if (this.clickHandler) this.container.removeEventListener("click", this.clickHandler);
    this.balls.forEach((b) => b.destroy());
    this.pets.forEach((p) => p.destroyImmediate());
    this.pets = [];
    this.cats = [];
    this.balls = [];
  }
}
