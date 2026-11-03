"""Mot de passe oublié : réinitialisation sur le PC où CamSup est installé.

Seul un administrateur de Windows peut le faire (les données de CamSup lui sont réservées) : c'est cette autorisation
qui remplace l'ancien mot de passe. Programme Windows : menu Démarrer > « CamSup — mot de passe oublié »
(`CamSup.exe --mot-de-passe-oublie`, fenêtre). Développement : `python -m app.recovery` (questions dans le terminal).
"""
import getpass
import sys

from . import accounts, db


def users() -> list[dict]:
    db.init()
    return db.q("SELECT id, username, role, active FROM users ORDER BY role, username")


def reset(username: str, password: str) -> None:
    """Nouveau mot de passe ; compte réactivé, ses sessions fermées. `ValueError` lisible sinon."""
    db.init()
    if len(password) < 10:
        raise ValueError("Mot de passe trop court (10 caractères minimum)")
    row = db.one("SELECT id, username FROM users WHERE username=?", (username,))
    if not row:
        raise ValueError(f"Compte « {username} » introuvable")
    db.x("UPDATE users SET pw_hash=?, active=1 WHERE id=?", (accounts.hash_password(password), row["id"]))
    db.x("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    accounts.audit(None, "mot_de_passe.reinitialise", obj=row["username"], detail="sur le PC (administrateur Windows)")


def main() -> None:
    rows = users()
    if not rows:
        print("Aucun compte : ouvrez l'interface de CamSup pour créer le vôtre.")
        return
    for r in rows:
        print(f"  {r['username']}  ({'intégrateur' if r['role'] == 'integrator' else 'pharmacien'})")
    name = input("Compte à réinitialiser : ").strip()
    pw = getpass.getpass("Nouveau mot de passe (10 caractères minimum) : ")
    if pw != getpass.getpass("Confirmation : "):
        sys.exit("Les deux mots de passe sont différents")
    try:
        reset(name, pw)
    except ValueError as e:
        sys.exit(str(e))
    print(f"Mot de passe de « {name} » changé : connectez-vous avec le nouveau.")


if __name__ == "__main__":
    main()
