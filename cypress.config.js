module.exports = {
	adminPassword: "admin",
	defaultCommandTimeout: 20000,
	pageLoadTimeout: 60000,
	video: false,
	viewportHeight: 960,
	viewportWidth: 1400,
	retries: {
		runMode: 1,
		openMode: 0,
	},
	e2e: {
		baseUrl: "http://localhost:8000",
		specPattern: "cypress/integration/*.js",
		supportFile: "cypress/support/index.js",
		testIsolation: false,
		setupNodeEvents(on, config) {
			return config;
		},
	},
};
