from app import app, db, User

with app.app_context():
    # Находим пользователей с пустым email
    users_without_email = User.query.filter(User.email == None).all()
    
    if users_without_email:
        print(f"Найдено {len(users_without_email)} пользователей с пустым email")
        for user in users_without_email:
            print(f"Удаляем пользователя: {user.username}")
            db.session.delete(user)
        
        db.session.commit()
        print("База данных очищена от некорректных записей")
    else:
        print("Некорректных записей не найдено")
    
    print("✅ База данных готова к работе")