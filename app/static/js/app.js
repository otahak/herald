document.addEventListener("DOMContentLoaded", () => {
    // Only mount demo component if #demo element exists
    const demoEl = document.getElementById("demo");
    if (demoEl) {
        const { createApp } = Vue;

        const DemoComponent = {
            data() {
                return {
                    message: "✅ Vue is reactive!",
                    count: 0
                };
            },
            methods: {
                increment() {
                    this.count++;
                }
            }
        };

        const app = createApp(DemoComponent);
        app.config.compilerOptions.delimiters = ["[[", "]]"];
        app.mount("#demo");
    }
});
