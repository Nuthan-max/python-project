/* RescueRoute motion and pointer-lighting helpers. */
(() => {
  "use strict";

  const gsap = window.gsap;
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const prefersReducedMotion = () => reducedQuery.matches;

  if (gsap && window.Flip) gsap.registerPlugin(window.Flip);

  function moveNavigationIndicator(button) {
    const nav = button?.closest(".nav-pills");
    const indicator = nav?.querySelector(".nav-indicator");
    if (!nav || !indicator) return;

    const bounds = {
      left: button.offsetLeft,
      top: button.offsetTop,
      width: button.offsetWidth,
      height: button.offsetHeight,
    };

    if (!gsap || prefersReducedMotion()) {
      Object.assign(indicator.style, {
        left: `${bounds.left}px`,
        top: `${bounds.top}px`,
        width: `${bounds.width}px`,
        height: `${bounds.height}px`,
      });
      return;
    }

    if (window.Flip && indicator.offsetWidth > 0) {
      const state = window.Flip.getState(indicator);
      Object.assign(indicator.style, {
        left: `${bounds.left}px`,
        top: `${bounds.top}px`,
        width: `${bounds.width}px`,
        height: `${bounds.height}px`,
      });
      window.Flip.from(state, {
        duration: 0.52,
        ease: "back.out(1.28)",
        absolute: false,
        prune: true,
      });
      return;
    }

    gsap.to(indicator, {
      ...bounds,
      duration: 0.48,
      ease: "back.out(1.25)",
      overwrite: true,
    });
  }

  function enter(loader) {
    const map = document.getElementById("map");
    const body = document.body;
    if (gsap && !prefersReducedMotion()) gsap.set(map, { opacity: 0 });
    body.classList.remove("is-starting");
    body.classList.add("is-ready");
    if (!loader) return;

    if (!gsap || prefersReducedMotion()) {
      loader.classList.add("loaded");
      loader.setAttribute("aria-hidden", "true");
      return;
    }

    gsap.set(loader, { transformOrigin: "50% 45%" });
    const timeline = gsap.timeline({
      defaults: { ease: "power2.out" },
      onComplete: () => {
        loader.classList.add("loaded");
        loader.setAttribute("aria-hidden", "true");
        gsap.set([".app-nav", ".floating-mode-bar", ".contextual-panel", ".floating-map-controls"], {
          clearProps: "transform,opacity",
        });
      },
    });

    timeline
      .to(loader, { autoAlpha: 0, scale: 0.985, duration: 0.34 })
      .to(map, { opacity: 1, duration: 0.52 }, "-=0.16")
      .from(".app-nav", { y: -14, autoAlpha: 0, duration: 0.42 }, "<")
      .from(".floating-mode-bar", { y: -9, autoAlpha: 0, duration: 0.4 }, "-=0.22")
      .from(".contextual-panel.open", { x: -24, autoAlpha: 0, duration: 0.48, ease: "back.out(1.12)" }, "-=0.22")
      .from(".contextual-panel.open .workflow-card", {
        y: 9,
        autoAlpha: 0,
        duration: 0.3,
        stagger: 0.055,
        ease: "power2.out",
      }, "-=0.18")
      .from(".floating-map-controls", { y: 10, autoAlpha: 0, duration: 0.36 }, "-=0.28");

    const activeTab = document.querySelector(".nav-pill.active");
    if (activeTab) moveNavigationIndicator(activeTab);
  }

  function panelOpened(panel) {
    if (!gsap || prefersReducedMotion() || !panel) return;
    const items = panel.querySelectorAll(".workspace-section.active .workflow-card");
    gsap.fromTo(items,
      { y: 8, autoAlpha: 0 },
      { y: 0, autoAlpha: 1, duration: 0.32, stagger: 0.045, ease: "power2.out", clearProps: "transform,opacity" });
  }

  function switchContent(current, next, change) {
    if (!next) return;
    const sections = Array.from(document.querySelectorAll(".workspace-section"));
    if (!gsap || prefersReducedMotion()) {
      sections.forEach((section) => {
        section.style.removeProperty("opacity");
        section.style.removeProperty("visibility");
        section.style.removeProperty("transform");
      });
      change();
      if (!prefersReducedMotion() && next) {
        next.classList.add("motion-enter");
        next.addEventListener("animationend", () => next.classList.remove("motion-enter"), { once: true });
      }
      return;
    }

    gsap.killTweensOf(sections);
    gsap.set(sections, { clearProps: "transform,opacity,visibility" });
    const reveal = () => {
      change();
      gsap.fromTo(next,
        { autoAlpha: 0, y: 8 },
        { autoAlpha: 1, y: 0, duration: 0.3, ease: "power2.out", clearProps: "transform,opacity,visibility" });
    };
    if (!current || current === next) {
      reveal();
    } else {
      gsap.to(current, {
        autoAlpha: 0,
        y: -4,
        duration: 0.13,
        ease: "power1.out",
        onComplete: () => {
        change();
          gsap.fromTo(next,
            { autoAlpha: 0, y: 8 },
            { autoAlpha: 1, y: 0, duration: 0.3, ease: "power2.out", clearProps: "transform,opacity,visibility" });
        },
      });
    }
  }

  function restoreContent(section) {
    if (!section) return;
    if (gsap) gsap.set(section, { clearProps: "transform,opacity,visibility" });
    else {
      section.style.removeProperty("opacity");
      section.style.removeProperty("visibility");
      section.style.removeProperty("transform");
    }
  }

  let litSurface = null;
  let magneticTarget = null;
  let parallaxTarget = null;
  let pointerFrame = 0;
  let latestPointer = null;

  function updatePointerEffects(event) {
    latestPointer = event;
    if (pointerFrame) return;
    pointerFrame = window.requestAnimationFrame(() => {
      pointerFrame = 0;
      const pointer = latestPointer;
      if (!pointer || prefersReducedMotion() || pointer.pointerType === "touch") return;

      const surface = pointer.target instanceof Element ? pointer.target.closest(".glass-dynamic") : null;
      if (litSurface && litSurface !== surface) {
        litSurface.style.removeProperty("--light-x");
        litSurface.style.removeProperty("--light-y");
      }
      if (surface) {
        const rect = surface.getBoundingClientRect();
        surface.style.setProperty("--light-x", `${Math.round(pointer.clientX - rect.left)}px`);
        surface.style.setProperty("--light-y", `${Math.round(pointer.clientY - rect.top)}px`);
      }
      litSurface = surface;

      const magnetic = pointer.target instanceof Element ? pointer.target.closest(".magnetic") : null;
      if (magneticTarget && magneticTarget !== magnetic) {
        magneticTarget.style.removeProperty("--mag-x");
        magneticTarget.style.removeProperty("--mag-y");
      }
      if (magnetic) {
        const rect = magnetic.getBoundingClientRect();
        const attractionX = Math.max(-3.5, Math.min(3.5, (pointer.clientX - rect.left - rect.width / 2) * 0.08));
        const attractionY = Math.max(-3.5, Math.min(3.5, (pointer.clientY - rect.top - rect.height / 2) * 0.08));
        magnetic.style.setProperty("--mag-x", `${attractionX.toFixed(1)}px`);
        magnetic.style.setProperty("--mag-y", `${attractionY.toFixed(1)}px`);
      }
      magneticTarget = magnetic;

      const parallax = pointer.target instanceof Element ? pointer.target.closest(".parallax-depth") : null;
      if (parallaxTarget && parallaxTarget !== parallax) {
        parallaxTarget.style.removeProperty("--depth-x");
        parallaxTarget.style.removeProperty("--depth-y");
      }
      if (parallax) {
        const rect = parallax.getBoundingClientRect();
        const depthX = Math.max(-1.8, Math.min(1.8, (pointer.clientX - rect.left - rect.width / 2) * 0.025));
        const depthY = Math.max(-1.8, Math.min(1.8, (pointer.clientY - rect.top - rect.height / 2) * 0.025));
        parallax.style.setProperty("--depth-x", `${depthX.toFixed(1)}px`);
        parallax.style.setProperty("--depth-y", `${depthY.toFixed(1)}px`);
      }
      parallaxTarget = parallax;
    });
  }

  document.addEventListener("pointermove", updatePointerEffects, { passive: true });
  document.addEventListener("pointerleave", () => {
    litSurface?.style.removeProperty("--light-x");
    litSurface?.style.removeProperty("--light-y");
    magneticTarget?.style.removeProperty("--mag-x");
    magneticTarget?.style.removeProperty("--mag-y");
    parallaxTarget?.style.removeProperty("--depth-x");
    parallaxTarget?.style.removeProperty("--depth-y");
    litSurface = null;
    magneticTarget = null;
    parallaxTarget = null;
  });
  window.addEventListener("resize", () => {
    const activeTab = document.querySelector(".nav-pill.active");
    if (activeTab) moveNavigationIndicator(activeTab);
  }, { passive: true });

  const activeTab = document.querySelector(".nav-pill.active");
  if (activeTab) moveNavigationIndicator(activeTab);

  window.RescueMotion = {
    gsap,
    prefersReducedMotion,
    enter,
    moveNavigationIndicator,
    panelOpened,
    switchContent,
    restoreContent,
  };
})();
