/** @odoo-module **/

const STORAGE_KEY = "cdu.chatter.collapsed";

function isCollapsed() {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
}

function applyCollapsedState() {
    document.body.classList.toggle("cdu-chatter-collapsed", isCollapsed());
}

function getChatterContainers() {
    return document.querySelectorAll(".o_FormRenderer_chatterContainer, .oe_chatter");
}

function ensureToggle(chatter) {
    if (chatter.querySelector(".cdu-chatter-toggle")) {
        return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "cdu-chatter-toggle";
    button.title = "Collapse or expand chatter";
    button.innerHTML = '<span class="cdu-chatter-toggle-icon">›</span><span class="cdu-chatter-toggle-label">Chatter</span>';
    button.addEventListener("click", () => {
        window.localStorage.setItem(STORAGE_KEY, isCollapsed() ? "0" : "1");
        applyCollapsedState();
    });

    chatter.prepend(button);
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
