"""Every 10 minutes (cron, set up by autoupdate.sh): confirmed lineups for matches starting within the hour, then
re-predict so the picks use the real starting XI. Only a few API calls per match day.
If a data / model job is running the lineups are still stored; the next prediction picks them up."""
import db
import players
from apifootball import Client

if __name__ == "__main__":
    db.init_db()
    con = db.connect()
    got = players.poll_lineups(con, Client())
    if got:
        with db.JobLock() as free:
            if free:
                import model
                model.predict(con)
            else:
                print("a data / model job is running — it will use the new lineups", flush=True)
