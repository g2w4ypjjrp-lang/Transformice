package org.transformice;

// Imports
import java.io.IOException;
import java.util.ArrayList;
import org.fusesource.jansi.AnsiConsole;
import org.psframework.admin.PSAdminConfig;
import org.psframework.database.PSDatabaseConfig;
import org.psframework.database.PSDatabaseProvider;
import org.psframework.logging.PSLogger;
import org.psframework.utils.PSJson;
import org.transformice.config.TransformiceConfig;

public final class Application {
    private static final PSLogger applicationLogger = new PSLogger();
    private static TransformiceConfig applicationConfig;
    private static GameServer gameServer;

    /**
     * Entry point of the application.
     * @param args the command-line arguments passed to the application.
     */
    public static void main(String[] args) {
        try {
            AnsiConsole.systemInstall();
            applicationConfig = PSJson.readFile("./config/properties.json", TransformiceConfig.class);
            applicationLogger.info("Loaded the server config.");
            // Secrets can be supplied through environment variables so they never live in source/config files.
            var db = applicationConfig.getDBConfig();
            var adm = applicationConfig.getAdmPanelConfig();
            String dbHost = env("TFM_DB_HOST", db.getHost());
            int dbPort = Integer.parseInt(env("TFM_DB_PORT", String.valueOf(db.getPort() == -1 ? 27017 : db.getPort())));
            String dbName = env("TFM_DB_NAME", db.getName());
            String dbUser = env("TFM_DB_USERNAME", db.getUsername());
            String dbPass = env("TFM_DB_PASSWORD", db.getPassword());
            String admUser = env("TFM_ADMIN_USERNAME", adm.getUsername());
            String admPass = env("TFM_ADMIN_PASSWORD", adm.getPassword());
            if ("changeme".equals(admPass))
                applicationLogger.warn("Admin panel password is still the default 'changeme'. Set TFM_ADMIN_PASSWORD.");

            gameServer = new GameServer(new ArrayList<>(applicationConfig.getSWFConfig().getPorts()), new PSDatabaseConfig(PSDatabaseProvider.MONGODB, dbHost, dbPort, dbName, dbUser, dbPass), new PSAdminConfig(adm.getPort(), admUser, admPass), applicationLogger, applicationConfig);
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {gameServer.stop();}));
            gameServer.start();
        } catch (IOException ex) {
            applicationLogger.trace("Unable to load the server config.", ex);
        }


        AnsiConsole.systemUninstall();
    }

    /**
     * Reads an environment variable, falling back to the given value when it is unset or blank.
     */
    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return (value == null || value.isBlank()) ? fallback : value;
    }
}
