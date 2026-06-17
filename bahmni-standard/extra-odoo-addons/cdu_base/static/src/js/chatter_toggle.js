/** @odoo-module **/

const STORAGE_KEY = "cdu.chatter.collapsed.v2";
const CHATTER_SELECTOR = ".o_FormRenderer_chatterContainer, .o-mail-Form-chatter, .oe_chatter";

function isCollapsed() {
    const storedState = window.localStorage.getItem(STORAGE_KEY);
    return storedState === null ? true : storedState === "1";
}

function applyCollapsedState() {
    document.body.classList.toggle("cdu-chatter-collapsed", isCollapsed());
    document.querySelectorAll(".cdu-chatter-toggle").forEach((button) => {
        const collapsed = isCollapsed();
        button.setAttribute("aria-expanded", collapsed ? "false" : "true");
        button.title = collapsed ? "Show chatter" : "Hide chatter";
    });
}

function getChatterContainers() {
    const containers = [...document.querySelectorAll(CHATTER_SELECTOR)];
    return containers.filter((container) => !container.parentElement?.closest(CHATTER_SELECTOR));
}

function ensureToggle(chatter) {
    if (chatter.querySelector(".cdu-chatter-toggle")) {
        return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "cdu-chatter-toggle";
    button.setAttribute("aria-label", "Toggle chatter");
    button.innerHTML = '<span class="cdu-chatter-toggle-icon">›</span><span class="cdu-chatter-toggle-label">Chatter</span>';
    button.addEventListener("click", () => {
        window.localStorage.setItem(STORAGE_KEY, isCollapsed() ? "0" : "1");
        applyCollapsedState();
    });

    chatter.prepend(button);
    applyCollapsedState();
}

function setupChatterToggles() {
    applyCollapsedState();
    getChatterContainers().forEach(ensureToggle);
}

const observer = new MutationObserver(setupChatterToggles);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
        setupChatterToggles();
        observer.observe(document.body, {childList: true, subtree: true});
    });
} else {
    setupChatterToggles();
    observer.observe(document.body, {childList: true, subtree: true});
}
