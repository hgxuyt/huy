from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
import json
import logging
from datetime import datetime
import threading
import time
import requests
from itsdangerous import URLSafeTimedSerializer
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import random
import string

# Загрузка переменных окружения
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///social_network.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Настройки для email
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@psychoolds.com')

# Сериализатор для токенов
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# Хранилище для временных кодов (в реальном проекте используйте Redis или БД)
temp_codes = {}

# Инициализация расширений
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите для доступа к этой странице'

logging.basicConfig(level=logging.INFO)

# ========== МОДЕЛИ БАЗЫ ДАННЫХ ==========
# Модели базы данных - УПРОЩЕННАЯ ВЕРСИЯ
class User(UserMixin, db.Model):
    """Модель пользователя социальной сети"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    avatar = db.Column(db.String(200), default='default.jpg')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Связи - используем простые backref
    posts = db.relationship('Post', backref='author', lazy=True, cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='author', lazy=True, cascade='all, delete-orphan')
    likes = db.relationship('Like', backref='user', lazy=True, cascade='all, delete-orphan')
    
    # Подписки
    followed = db.relationship(
        'Follow', foreign_keys='Follow.follower_id',
        backref='follower', lazy='dynamic'
    )
    followers = db.relationship(
        'Follow', foreign_keys='Follow.followed_id',
        backref='followed', lazy='dynamic'
    )
    
    def __repr__(self):
        return f'<User {self.username}>'

class Post(db.Model):
    """Модель поста"""
    __tablename__ = 'posts'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Связи
    comments = db.relationship('Comment', backref='post', lazy=True, cascade='all, delete-orphan')
    likes = db.relationship('Like', backref='post', lazy=True, cascade='all, delete-orphan')
    
    @property
    def like_count(self):
        return len(self.likes)
    
    @property
    def comment_count(self):
        return len(self.comments)

class Comment(db.Model):
    """Модель комментария"""
    __tablename__ = 'comments'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<Comment {self.id}>'

class Like(db.Model):
    """Модель лайка"""
    __tablename__ = 'likes'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='unique_user_post_like'),)

class Follow(db.Model):
    """Модель подписок"""
    __tablename__ = 'follows'
    
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('follower_id', 'followed_id', name='unique_follow'),)

class Message(db.Model):
    """Модель личных сообщений"""
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    # Связи
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='received_messages')
    
    def __repr__(self):
        return f'<Message {self.id}>'

# Загрузка пользователя для Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Создание таблиц БД
with app.app_context():
    db.create_all()
    print("✅ База данных создана/проверена")

    

# ========== НАСТРОЙКА OPENROUTER AI ==========

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_ENABLED = False
AI_MODEL = None

if OPENROUTER_API_KEY:
    try:
        # Проверяем подключение к OpenRouter
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Пробуем получить список моделей
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            models = response.json()
            # Доступные бесплатные модели
            free_models = [
                "google/gemini-2.5-flash",
                "google/gemini-2.0-flash",
                "mistralai/mistral-7b-instruct",
                "meta-llama/llama-3-8b-instruct"
            ]
            
            # Выбираем первую доступную модель
            AI_MODEL = free_models[0]
            AI_ENABLED = True
            logging.info(f"✅ OpenRouter AI настроен, модель: {AI_MODEL}")
        else:
            logging.error(f"❌ Ошибка OpenRouter: {response.status_code}")
            
    except Exception as e:
        logging.error(f"❌ Ошибка настройки OpenRouter: {e}")
        AI_ENABLED = False
else:
    logging.warning("⚠️ OPENROUTER_API_KEY не найден в .env файле")


# ========== МАРШРУТЫ АУТЕНТИФИКАЦИИ ==========

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Регистрация нового пользователя"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not username or not email or not password:
            flash('Все поля обязательны для заполнения', 'danger')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Пароли не совпадают', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(username=username).first():
            flash('Имя пользователя уже занято', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email уже зарегистрирован', 'danger')
            return redirect(url_for('register'))
        
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            bio=None,
            avatar='default.jpg'
        )
        
        try:
            db.session.add(user)
            db.session.commit()
            flash('Регистрация успешна! Теперь вы можете войти', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при регистрации: {str(e)}', 'danger')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Вход в систему"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get('next')
            flash(f'Добро пожаловать, {user.username}!', 'success')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Выход из системы"""
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

# ========== МАРШРУТЫ СОЦИАЛЬНОЙ СЕТИ ==========

@app.route('/')
@app.route('/index')
def index():
    """Главная страница с лентой постов"""
    page = request.args.get('page', 1, type=int)
    filter_type = request.args.get('filter', 'all')
    
    # Статистика для сайдбара
    user_count = User.query.count()
    post_count = Post.query.count()
    
    if filter_type == 'following' and current_user.is_authenticated:
        # Только подписки
        followed_ids = [f.followed_id for f in current_user.followed.all()]
        followed_ids.append(current_user.id)
        posts = Post.query.filter(Post.user_id.in_(followed_ids)) \
            .order_by(Post.timestamp.desc()) \
            .paginate(page=page, per_page=10, error_out=False)
    
    elif filter_type == 'popular':
        # Получаем все посты
        all_posts = Post.query.all()
        # Сортируем по количеству лайков
        sorted_posts = sorted(all_posts, key=lambda p: len(p.likes), reverse=True)
        # Ручная пагинация
        start = (page - 1) * 10
        end = start + 10
        posts_page = sorted_posts[start:end]
        
        # Создаем объект, похожий на Paginate
        class Paginate:
            def __init__(self, items, page, total, per_page=10):
                self.items = items
                self.page = page
                self.total = total
                self.per_page = per_page
                self.pages = (total + per_page - 1) // per_page
            
            def has_prev(self):
                return self.page > 1
            
            def has_next(self):
                return self.page < self.pages
            
            def prev_num(self):
                return self.page - 1
            
            def next_num(self):
                return self.page + 1
            
            def iter_pages(self):
                return range(1, self.pages + 1)
        
        posts = Paginate(posts_page, page, len(all_posts))
    
    else:
        # Все посты
        posts = Post.query.order_by(Post.timestamp.desc()) \
            .paginate(page=page, per_page=10, error_out=False)
    
    return render_template('index.html', 
                         posts=posts, 
                         filter_type=filter_type,
                         user_count=user_count, 
                         post_count=post_count)

@app.route('/profile/<username>')
def profile(username):
    """Страница профиля пользователя"""
    user = User.query.filter_by(username=username).first_or_404()
    
    # Проверка подписки
    is_following = False
    if current_user.is_authenticated and current_user != user:
        is_following = Follow.query.filter_by(
            follower_id=current_user.id,
            followed_id=user.id
        ).first() is not None
    
    # Определяем активную вкладку
    tab = request.args.get('tab', 'posts')
    page = request.args.get('page', 1, type=int)
    
    if tab == 'comments' and current_user.is_authenticated and current_user == user:
        # Получаем все комментарии к постам пользователя
        user_post_ids = [p.id for p in user.posts]
        
        # Если есть посты, получаем комментарии к ним
        if user_post_ids:
            comments = Comment.query.filter(Comment.post_id.in_(user_post_ids)) \
                .order_by(Comment.timestamp.desc()) \
                .paginate(page=page, per_page=20, error_out=False)
            
            # Считаем непрочитанные комментарии
            unread_comments = Comment.query.filter(
                Comment.post_id.in_(user_post_ids),
                Comment.is_read == False,
                Comment.user_id != user.id
            ).count()
        else:
            comments = None
            unread_comments = 0
        
        return render_template('profile.html', 
                              profile_user=user, 
                              posts=None,
                              comments=comments,
                              is_following=is_following,
                              active_tab='comments',
                              unread_comments_count=unread_comments)
    else:
        # Посты пользователя с пагинацией
        posts = Post.query.filter_by(user_id=user.id) \
            .order_by(Post.timestamp.desc()) \
            .paginate(page=page, per_page=10, error_out=False)
        
        # Считаем непрочитанные комментарии (только для своего профиля)
        unread_comments = 0
        if current_user.is_authenticated and current_user == user:
            user_post_ids = [p.id for p in user.posts]
            if user_post_ids:
                unread_comments = Comment.query.filter(
                    Comment.post_id.in_(user_post_ids),
                    Comment.is_read == False,
                    Comment.user_id != user.id
                ).count()
        
        return render_template('profile.html', 
                              profile_user=user, 
                              posts=posts,
                              comments=None,
                              is_following=is_following,
                              active_tab='posts',
                              unread_comments_count=unread_comments)

from werkzeug.utils import secure_filename
import os

# Настройки для загрузки файлов
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# Создаем папку для загрузок, если её нет
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/comments/mark-read', methods=['POST'])
@login_required
def mark_comments_read():
    """Отметить все комментарии к постам пользователя как прочитанные"""
    user_post_ids = [p.id for p in current_user.user_posts]
    Comment.query.filter(
        Comment.post_id.in_(user_post_ids),
        Comment.user_id != current_user.id,  # Было author_id, стало user_id
        Comment.is_read == False
    ).update({Comment.is_read: True})
    db.session.commit()
    
    return jsonify({'success': True})

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """Редактирование профиля пользователя"""
    if request.method == 'POST':
        # Получаем данные из формы
        username = request.form.get('username')
        email = request.form.get('email')
        bio = request.form.get('bio', '')
        
        # Проверка уникальности имени пользователя
        if username != current_user.username:
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                flash('Это имя пользователя уже занято', 'danger')
                return redirect(url_for('edit_profile'))
        
        # Проверка уникальности email
        if email != current_user.email:
            existing_email = User.query.filter_by(email=email).first()
            if existing_email:
                flash('Этот email уже зарегистрирован', 'danger')
                return redirect(url_for('edit_profile'))
        
        # Обновляем данные
        current_user.username = username
        current_user.email = email
        current_user.bio = bio
        
             # Обработка загрузки аватара
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename and allowed_file(file.filename):
                # Удаляем старый аватар, если это не дефолтный
                if current_user.avatar != 'default.jpg':
                    old_avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.avatar)
                    if os.path.exists(old_avatar_path):
                        os.remove(old_avatar_path)
                
                # Безопасное имя файла
                filename = secure_filename(f"user_{current_user.id}_{int(time.time())}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.avatar = filename
                flash('Аватар обновлен', 'success')
        
        db.session.commit()
        flash('Профиль успешно обновлен!', 'success')
        return redirect(url_for('profile', username=current_user.username))
    
    return render_template('edit_profile.html')

@app.route('/post/new', methods=['POST'])
@login_required
def create_post():
    """Создание нового поста"""
    content = request.form.get('content')
    
    if content and content.strip():
        post = Post(content=content, author=current_user)
        db.session.add(post)
        db.session.commit()
        flash('Пост опубликован!', 'success')
    else:
        flash('Пост не может быть пустым', 'danger')
    
    return redirect(url_for('index'))

@app.route('/post/<int:post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    """Лайк/дизлайк поста (AJAX)"""
    post = Post.query.get_or_404(post_id)
    
    like = Like.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    
    if like:
        db.session.delete(like)
        db.session.commit()
        liked = False
    else:
        like = Like(user_id=current_user.id, post_id=post_id)
        db.session.add(like)
        db.session.commit()
        liked = True
    
    return jsonify({
        'liked': liked,
        'like_count': post.like_count
    })

@app.route('/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    """Добавление комментария"""
    try:
        post = Post.query.get_or_404(post_id)
        content = request.form.get('content', '').strip()
        
        if not content:
            flash('Комментарий не может быть пустым', 'danger')
            return redirect(request.referrer or url_for('index'))
        
        comment = Comment(
            content=content,
            user_id=current_user.id,
            post_id=post_id,
            is_read=False
        )
        
        db.session.add(comment)
        db.session.commit()
        
        flash('Комментарий добавлен', 'success')
        
        # Просто редирект на ту же страницу без якоря
        referrer = request.referrer
        if referrer:
            # Убираем якорь из URL
            base_url = referrer.split('#')[0]
            return redirect(base_url)
        else:
            return redirect(url_for('index'))
            
    except Exception as e:
        print(f"❌ ОШИБКА при добавлении комментария: {str(e)}")
        db.session.rollback()
        flash(f'Ошибка при добавлении комментария: {str(e)}', 'danger')
        return redirect(request.referrer or url_for('index'))

@app.route('/follow/<username>')
@login_required
def follow_user(username):
    """Подписаться на пользователя"""
    user = User.query.filter_by(username=username).first()
    
    if user is None:
        flash('Пользователь не найден', 'danger')
        return redirect(url_for('index'))
    
    if user == current_user:
        flash('Вы не можете подписаться на себя', 'warning')
        return redirect(url_for('profile', username=username))
    
    existing_follow = Follow.query.filter_by(
        follower_id=current_user.id,
        followed_id=user.id
    ).first()
    
    if existing_follow:
        flash(f'Вы уже подписаны на {user.username}', 'info')
    else:
        follow = Follow(follower_id=current_user.id, followed_id=user.id)
        db.session.add(follow)
        db.session.commit()
        flash(f'Вы подписались на {user.username}', 'success')
    
    return redirect(url_for('profile', username=username))

@app.route('/unfollow/<username>')
@login_required
def unfollow_user(username):
    """Отписаться от пользователя"""
    user = User.query.filter_by(username=username).first()
    
    if user is None:
        flash('Пользователь не найден', 'danger')
        return redirect(url_for('index'))
    
    if user == current_user:
        flash('Вы не можете отписаться от себя', 'warning')
        return redirect(url_for('profile', username=username))
    
    follow = Follow.query.filter_by(
        follower_id=current_user.id,
        followed_id=user.id
    ).first()
    
    if follow:
        db.session.delete(follow)
        db.session.commit()
        flash(f'Вы отписались от {user.username}', 'success')
    else:
        flash(f'Вы не были подписаны на {user.username}', 'info')
    
    return redirect(url_for('profile', username=username))

# ========== ЛИЧНЫЕ СООБЩЕНИЯ ==========

@app.route('/messages')
@login_required
def messages():
    """Страница со списком диалогов"""
    # Получаем всех пользователей, с которыми есть переписка
    sent_users = db.session.query(Message.recipient_id).filter(Message.sender_id == current_user.id).distinct()
    received_users = db.session.query(Message.sender_id).filter(Message.recipient_id == current_user.id).distinct()
    
    user_ids = set([u[0] for u in sent_users] + [u[0] for u in received_users])
    conversations = []
    
    for user_id in user_ids:
        other_user = User.query.get(user_id)
        if other_user:
            # Получаем последнее сообщение
            last_message = Message.query.filter(
                ((Message.sender_id == current_user.id) & (Message.recipient_id == user_id)) |
                ((Message.sender_id == user_id) & (Message.recipient_id == current_user.id))
            ).order_by(Message.timestamp.desc()).first()
            
            # Считаем непрочитанные
            unread_count = Message.query.filter_by(
                sender_id=user_id,
                recipient_id=current_user.id,
                is_read=False
            ).count()
            
            conversations.append({
                'user': other_user,
                'last_message': last_message,
                'unread_count': unread_count
            })
    
    # Сортируем по времени последнего сообщения
    conversations.sort(key=lambda x: x['last_message'].timestamp if x['last_message'] else datetime.min, reverse=True)
    
    return render_template('messages.html', conversations=conversations)

@app.route('/messages/<int:user_id>')
@login_required
def conversation(user_id):
    """Страница переписки с конкретным пользователем"""
    other_user = User.query.get_or_404(user_id)
    
    if other_user == current_user:
        flash('Нельзя отправлять сообщения самому себе', 'warning')
        return redirect(url_for('messages'))
    
    # Получаем все сообщения между пользователями
    messages = Message.query.filter(
        ((Message.sender_id == current_user.id) & (Message.recipient_id == user_id)) |
        ((Message.sender_id == user_id) & (Message.recipient_id == current_user.id))
    ).order_by(Message.timestamp.asc()).all()
    
    # Отмечаем сообщения как прочитанные
    unread = Message.query.filter_by(
        sender_id=user_id,
        recipient_id=current_user.id,
        is_read=False
    ).all()
    
    for msg in unread:
        msg.is_read = True
    db.session.commit()
    
    return render_template('conversation.html', other_user=other_user, messages=messages)

@app.route('/send_message/<int:recipient_id>', methods=['POST'])
@login_required
def send_message(recipient_id):
    """Отправка сообщения"""
    recipient = User.query.get_or_404(recipient_id)
    
    if recipient == current_user:
        flash('Нельзя отправлять сообщения самому себе', 'warning')
        return redirect(url_for('messages'))
    
    content = request.form.get('content', '').strip()
    
    if not content:
        flash('Сообщение не может быть пустым', 'danger')
        return redirect(url_for('conversation', user_id=recipient_id))
    
    message = Message(
        sender_id=current_user.id,
        recipient_id=recipient_id,
        content=content
    )
    
    db.session.add(message)
    db.session.commit()
    
    flash('Сообщение отправлено', 'success')
    return redirect(url_for('conversation', user_id=recipient_id))

@app.route('/api/unread_count')
@login_required
def unread_count():
    """API для получения количества непрочитанных сообщений"""
    count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()
    
    return jsonify({'count': count})

@app.route('/api/send_message/<int:recipient_id>', methods=['POST'])
@login_required
def api_send_message(recipient_id):
    """API для отправки сообщения (AJAX)"""
    recipient = User.query.get_or_404(recipient_id)
    
    if recipient == current_user:
        return jsonify({'error': 'Нельзя отправлять сообщения самому себе'}), 400
    
    data = request.json
    content = data.get('content', '').strip()
    
    if not content:
        return jsonify({'error': 'Сообщение не может быть пустым'}), 400
    
    message = Message(
        sender_id=current_user.id,
        recipient_id=recipient_id,
        content=content
    )
    
    db.session.add(message)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': {
            'id': message.id,
            'content': message.content,
            'timestamp': message.timestamp.strftime('%H:%M'),
            'sender_id': message.sender_id
        }
    })

@app.route('/api/chats')
@login_required
def api_get_chats():
    """API для получения списка чатов пользователя"""
    try:
        # Получаем всех пользователей, с которыми есть переписка
        sent_users = db.session.query(Message.recipient_id).filter(Message.sender_id == current_user.id).distinct()
        received_users = db.session.query(Message.sender_id).filter(Message.recipient_id == current_user.id).distinct()
        
        user_ids = set([u[0] for u in sent_users] + [u[0] for u in received_users])
        chats = []
        
        for user_id in user_ids:
            other_user = User.query.get(user_id)
            if other_user:
                # Получаем последнее сообщение
                last_message = Message.query.filter(
                    ((Message.sender_id == current_user.id) & (Message.recipient_id == user_id)) |
                    ((Message.sender_id == user_id) & (Message.recipient_id == current_user.id))
                ).order_by(Message.timestamp.desc()).first()
                
                # Считаем непрочитанные
                unread_count = Message.query.filter_by(
                    sender_id=user_id,
                    recipient_id=current_user.id,
                    is_read=False
                ).count()
                
                chats.append({
                    'userId': other_user.id,
                    'userName': other_user.username,
                    'userAvatar': other_user.avatar,
                    'lastMessage': {
                        'content': last_message.content if last_message else None,
                        'timestamp': last_message.timestamp.isoformat() if last_message else None,
                        'sender_id': last_message.sender_id if last_message else None
                    } if last_message else None,
                    'unreadCount': unread_count
                })
        
        # Сортируем по времени последнего сообщения
        chats.sort(key=lambda x: x['lastMessage']['timestamp'] if x['lastMessage'] else '', reverse=True)
        
        return jsonify({'success': True, 'chats': chats})
        
    except Exception as e:
        logging.error(f"Ошибка получения чатов: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/messages/<int:user_id>')
@login_required
def api_get_messages(user_id):
    """API для получения сообщений с конкретным пользователем"""
    try:
        other_user = User.query.get_or_404(user_id)
        
        # Получаем все сообщения между пользователями
        messages = Message.query.filter(
            ((Message.sender_id == current_user.id) & (Message.recipient_id == user_id)) |
            ((Message.sender_id == user_id) & (Message.recipient_id == current_user.id))
        ).order_by(Message.timestamp.asc()).all()
        
        messages_list = []
        for msg in messages:
            sender = User.query.get(msg.sender_id)
            messages_list.append({
                'id': msg.id,
                'content': msg.content,
                'timestamp': msg.timestamp.isoformat(),
                'sender_id': msg.sender_id,
                'sender_name': sender.username if sender else 'Unknown',
                'is_read': msg.is_read
            })
        
        return jsonify({'success': True, 'messages': messages_list})
        
    except Exception as e:
        logging.error(f"Ошибка получения сообщений: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/mark-read/<int:user_id>', methods=['POST'])
@login_required
def api_mark_messages_read(user_id):
    """API для отметки сообщений как прочитанных"""
    try:
        # Отмечаем сообщения как прочитанные
        unread = Message.query.filter_by(
            sender_id=user_id,
            recipient_id=current_user.id,
            is_read=False
        ).all()
        
        for msg in unread:
            msg.is_read = True
        
        db.session.commit()
        
        return jsonify({'success': True, 'count': len(unread)})
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Ошибка отметки сообщений: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== AI ЧАТ через OpenRouter ==========

@app.route('/ai-chat')
@login_required
def ai_chat():
    """Страница с AI-ассистентом"""
    return render_template('ai_chat.html', 
                         ai_enabled=AI_ENABLED,
                         now=datetime.now())

def generate_reset_code(length=6):
    """Генерирует случайный числовой код"""
    return ''.join(random.choices(string.digits, k=length))

def send_reset_email(recipient_email, reset_code):
    """Отправляет код сброса пароля на email"""
    try:
        # Настройки для Gmail (или другого SMTP)
        smtp_server = app.config['MAIL_SERVER']
        smtp_port = app.config['MAIL_PORT']
        sender_email = app.config['MAIL_USERNAME']
        sender_password = app.config['MAIL_PASSWORD']
        
        # Создаем сообщение
        message = MIMEMultipart('alternative')
        message['Subject'] = 'Код для сброса пароля - Psychoolds'
        message['From'] = sender_email
        message['To'] = recipient_email
        
        # Текстовая версия
        text = f"""
        Здравствуйте!
        
        Вы запросили смену пароля в социальной сети Psychoolds.
        Ваш код подтверждения: {reset_code}
        
        Если вы не запрашивали смену пароля, просто проигнорируйте это письмо.
        
        С уважением,
        Команда Psychoolds
        """
        
        # HTML версия
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #4361ee, #3a56d4); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px; }}
                .code {{ background: white; font-size: 32px; font-weight: bold; color: #4361ee; padding: 15px; text-align: center; border-radius: 10px; margin: 20px 0; letter-spacing: 5px; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #6c757d; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🔄 Сброс пароля</h2>
                </div>
                <div class="content">
                    <p>Здравствуйте!</p>
                    <p>Вы запросили смену пароля в социальной сети <strong>Psychoolds</strong>.</p>
                    
                    <div class="code">
                        {reset_code}
                    </div>
                    
                    <p>Введите этот код в форме смены пароля. Код действителен в течение 15 минут.</p>
                    
                    <p><small>Если вы не запрашивали смену пароля, просто проигнорируйте это письмо.</small></p>
                </div>
                <div class="footer">
                    <p>С уважением,<br>Команда Psychoolds</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Прикрепляем версии
        message.attach(MIMEText(text, 'plain'))
        message.attach(MIMEText(html, 'html'))
        
        # Отправляем
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)
            
        return True, "Код отправлен на email"
        
    except Exception as e:
        logging.error(f"Ошибка отправки email: {str(e)}")
        return False, f"Ошибка отправки: {str(e)}"

def cleanup_expired_codes():
    """Очищает просроченные коды для смены пароля и email"""
    current_time = time.time()
    expired = []
    
    for key, value in list(temp_codes.items()):
        # Проверяем, является ли ключ числом (для смены пароля) или строкой (для смены email)
        if isinstance(key, int):
            # Для смены пароля - ключ это число (user_id)
            code, timestamp = value
            if current_time - timestamp > 900:  # 15 минут
                expired.append(key)
        elif isinstance(key, str) and key.startswith('email_change_'):
            # Для смены email - ключ это строка
            if current_time - value['timestamp'] > 900:
                expired.append(key)
    
    for key in expired:
        del temp_codes[key]

@app.route('/send-reset-code', methods=['POST'])
@login_required
def send_reset_code():
    """Отправляет код подтверждения на email"""
    try:
        # Генерируем код
        reset_code = generate_reset_code()
        
        # Сохраняем код с временной меткой
        temp_codes[current_user.id] = (reset_code, time.time())
        
        # Отправляем код на email
        success, message = send_reset_email(current_user.email, reset_code)
        
        if success:
            # Очищаем просроченные коды
            cleanup_expired_codes()
            return jsonify({
                'success': True,
                'message': 'Код подтверждения отправлен на ваш email'
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 500
            
    except Exception as e:
        logging.error(f"Ошибка при отправке кода: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Ошибка: {str(e)}'
        }), 500

# Хранилище для временных кодов сброса пароля (расширяем существующее)
# Уже есть temp_codes, но добавим отдельную структуру для сброса по email
# (или используем существующую - она уже подходит)

@app.route('/forgot-password-send-code', methods=['POST'])
def forgot_password_send_code():
    """Отправляет код подтверждения на email для сброса пароля (без авторизации)"""
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        
        # Проверка формата email
        if not email or '@' not in email or '.' not in email:
            return jsonify({
                'success': False,
                'message': 'Введите корректный email адрес'
            }), 400
        
        # Ищем пользователя по email
        user = User.query.filter_by(email=email).first()
        
        if not user:
            # Не говорим, что пользователь не найден (безопасность)
            # Но возвращаем успех, чтобы не раскрывать информацию
            return jsonify({
                'success': True,
                'message': 'Если пользователь с таким email существует, код будет отправлен'
            })
        
        # Генерируем код
        reset_code = generate_reset_code()
        
        # Сохраняем код с временной меткой (используем email как ключ)
        temp_codes[f"reset_{email}"] = {
            'code': reset_code,
            'user_id': user.id,
            'timestamp': time.time()
        }
        
        # Отправляем код на email
        success, message = send_password_reset_email(email, reset_code, user.username)
        
        if success:
            cleanup_expired_reset_codes()
            return jsonify({
                'success': True,
                'message': 'Код подтверждения отправлен на ваш email'
            })
        else:
            # Если отправка не удалась, удаляем сохраненный код
            if f"reset_{email}" in temp_codes:
                del temp_codes[f"reset_{email}"]
            return jsonify({
                'success': False,
                'message': message
            }), 500
            
    except Exception as e:
        logging.error(f"Ошибка при отправке кода сброса: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Ошибка: {str(e)}'
        }), 500

@app.route('/reset-password-with-code', methods=['POST'])
def reset_password_with_code():
    """Сброс пароля с подтверждением по коду (без авторизации)"""
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        code = data.get('code', '')
        new_password = data.get('new_password', '')
        
        cleanup_expired_reset_codes()
        
        key = f"reset_{email}"
        if key not in temp_codes:
            return jsonify({
                'success': False,
                'message': 'Код не найден. Запросите новый код.'
            }), 400
        
        code_data = temp_codes[key]
        
        # Проверяем код
        if code != code_data['code']:
            return jsonify({
                'success': False,
                'message': 'Неверный код'
            }), 400
        
        # Проверяем длину пароля
        if len(new_password) < 6:
            return jsonify({
                'success': False,
                'message': 'Пароль должен содержать минимум 6 символов'
            }), 400
        
        # Находим пользователя
        user = User.query.get(code_data['user_id'])
        if not user:
            return jsonify({
                'success': False,
                'message': 'Пользователь не найден'
            }), 404
        
        # Обновляем пароль
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        # Удаляем использованный код
        del temp_codes[key]
        
        # Отправляем уведомление о смене пароля
        try:
            send_password_change_notification(user.email, user.username)
        except:
            pass  # Не критично
        
        return jsonify({
            'success': True,
            'message': 'Пароль успешно изменен! Теперь вы можете войти с новым паролем.'
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Ошибка при сбросе пароля: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Ошибка: {str(e)}'
        }), 500

def send_password_reset_email(recipient_email, reset_code, username):
    """Отправляет код для сброса пароля на email"""
    try:
        smtp_server = app.config['MAIL_SERVER']
        smtp_port = app.config['MAIL_PORT']
        sender_email = app.config['MAIL_USERNAME']
        sender_password = app.config['MAIL_PASSWORD']
        
        message = MIMEMultipart('alternative')
        message['Subject'] = '🔐 Сброс пароля - Psychoolds'
        message['From'] = sender_email
        message['To'] = recipient_email
        
        text = f"""
        Здравствуйте, {username}!
        
        Вы запросили сброс пароля в социальной сети Psychoolds.
        
        Ваш код подтверждения: {reset_code}
        
        Введите этот код в форме сброса пароля. Код действителен в течение 15 минут.
        
        Если вы не запрашивали сброс пароля, просто проигнорируйте это письмо.
        
        С уважением,
        Команда Psychoolds
        """
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #f59e0b, #d97706); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px; }}
                .code {{ background: white; font-size: 32px; font-weight: bold; color: #f59e0b; padding: 15px; text-align: center; border-radius: 10px; margin: 20px 0; letter-spacing: 5px; }}
                .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #f59e0b; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #6c757d; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🔐 Сброс пароля</h2>
                </div>
                <div class="content">
                    <p>Здравствуйте, <strong>{username}</strong>!</p>
                    
                    <p>Вы запросили сброс пароля в социальной сети <strong>Psychoolds</strong>.</p>
                    
                    <div class="code">
                        {reset_code}
                    </div>
                    
                    <p>Введите этот код в форме сброса пароля. Код действителен в течение 15 минут.</p>
                    
                    <div class="warning">
                        <strong>⚠️ ВНИМАНИЕ!</strong> Если вы не запрашивали сброс пароля, 
                        просто проигнорируйте это письмо.
                    </div>
                </div>
                <div class="footer">
                    <p>С уважением,<br>Команда Psychoolds</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message.attach(MIMEText(text, 'plain'))
        message.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)
            
        return True, "Код отправлен на email"
        
    except Exception as e:
        logging.error(f"Ошибка отправки email сброса: {str(e)}")
        return False, f"Ошибка отправки: {str(e)}"

def send_password_change_notification(recipient_email, username):
    """Отправляет уведомление об успешной смене пароля"""
    try:
        smtp_server = app.config['MAIL_SERVER']
        smtp_port = app.config['MAIL_PORT']
        sender_email = app.config['MAIL_USERNAME']
        sender_password = app.config['MAIL_PASSWORD']
        
        message = MIMEMultipart('alternative')
        message['Subject'] = '✅ Пароль изменен - Psychoolds'
        message['From'] = sender_email
        message['To'] = recipient_email
        
        text = f"""
        Здравствуйте, {username}!
        
        Ваш пароль в социальной сети Psychoolds был успешно изменен.
        
        Если это были НЕ ВЫ, немедленно свяжитесь с поддержкой!
        
        С уважением,
        Команда Psychoolds
        """
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #10b981, #0d9488); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #10b981; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #6c757d; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>✅ Пароль успешно изменен</h2>
                </div>
                <div class="content">
                    <p>Здравствуйте, <strong>{username}</strong>!</p>
                    
                    <div class="success">
                        <strong>Ваш пароль в социальной сети Psychoolds был успешно изменен.</strong>
                    </div>
                    
                    <p><small>Если вы не совершали это действие, немедленно свяжитесь с поддержкой!</small></p>
                </div>
                <div class="footer">
                    <p>С уважением,<br>Команда Psychoolds</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message.attach(MIMEText(text, 'plain'))
        message.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)
            
        return True
        
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления о смене пароля: {str(e)}")
        return False

def cleanup_expired_reset_codes():
    """Очищает просроченные коды для сброса пароля"""
    current_time = time.time()
    expired = [key for key, data in temp_codes.items() 
               if key.startswith('reset_') and current_time - data['timestamp'] > 900]
    for key in expired:
        del temp_codes[key]

@app.route('/verify-reset-code', methods=['POST'])
@login_required
def verify_reset_code():
    """Проверяет код подтверждения"""
    data = request.json
    user_code = data.get('code', '')
    
    # Очищаем просроченные коды
    cleanup_expired_codes()
    
    # Проверяем наличие кода для пользователя
    if current_user.id not in temp_codes:
        return jsonify({
            'success': False,
            'message': 'Код не найден. Запросите новый код.'
        }), 400
    
    saved_code, timestamp = temp_codes[current_user.id]
    
    # Проверяем время (15 минут)
    if time.time() - timestamp > 900:
        del temp_codes[current_user.id]
        return jsonify({
            'success': False,
            'message': 'Код истек. Запросите новый код.'
        }), 400
    
    # Проверяем код
    if user_code == saved_code:
        return jsonify({
            'success': True,
            'message': 'Код подтвержден'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Неверный код'
        }), 400

@app.route('/change-password-with-code', methods=['POST'])
@login_required
def change_password_with_code():
    """Смена пароля с подтверждением по коду"""
    data = request.json
    code = data.get('code')
    new_password = data.get('new_password')
    
    # Проверяем код
    if current_user.id not in temp_codes:
        return jsonify({
            'success': False,
            'message': 'Код не найден. Запросите новый код.'
        }), 400
    
    saved_code, timestamp = temp_codes[current_user.id]
    
    # Проверяем время
    if time.time() - timestamp > 900:
        del temp_codes[current_user.id]
        return jsonify({
            'success': False,
            'message': 'Код истек. Запросите новый код.'
        }), 400
    
    # Проверяем код
    if code != saved_code:
        return jsonify({
            'success': False,
            'message': 'Неверный код'
        }), 400
    
    # Проверяем длину пароля
    if len(new_password) < 6:
        return jsonify({
            'success': False,
            'message': 'Пароль должен содержать минимум 6 символов'
        }), 400
    
    # Обновляем пароль
    try:
        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        # Удаляем использованный код
        del temp_codes[current_user.id]
        
        return jsonify({
            'success': True,
            'message': 'Пароль успешно изменен!'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Ошибка: {str(e)}'
        }), 500

@app.route('/send-email-change-code', methods=['POST'])
@login_required
def send_email_change_code():
    """Отправляет код подтверждения на СТАРЫЙ email для смены на новый"""
    try:
        data = request.json
        new_email = data.get('new_email', '').strip().lower()
        
        # Проверка формата email
        if '@' not in new_email or '.' not in new_email:
            return jsonify({
                'success': False,
                'message': 'Введите корректный email адрес'
            }), 400
        
        # Проверка, что email не занят
        existing_user = User.query.filter_by(email=new_email).first()
        if existing_user and existing_user.id != current_user.id:
            return jsonify({
                'success': False,
                'message': 'Этот email уже зарегистрирован'
            }), 400
        
        # Если это тот же email, что и текущий
        if new_email == current_user.email:
            return jsonify({
                'success': False,
                'message': 'Это ваш текущий email'
            }), 400
        
        # Генерируем код
        reset_code = generate_reset_code()
        
        # Сохраняем код с новым email и временной меткой
        # УБЕДИМСЯ, ЧТО NEW_EMAIL НЕ ПУСТОЙ
        temp_codes[f"email_change_{current_user.id}"] = {
            'code': reset_code,
            'new_email': new_email,  # Здесь должно быть значение!
            'timestamp': time.time()
        }
        
        # Отправляем код на СТАРЫЙ email
        success, message = send_email_change_confirmation_email(
            current_user.email, 
            reset_code, 
            current_user.username,
            new_email
        )
        
        if success:
            cleanup_expired_email_codes()
            return jsonify({
                'success': True,
                'message': f'Код подтверждения отправлен на ваш текущий email: {current_user.email}'
            })
        else:
            # Если отправка не удалась, удаляем сохраненный код
            if f"email_change_{current_user.id}" in temp_codes:
                del temp_codes[f"email_change_{current_user.id}"]
            return jsonify({
                'success': False,
                'message': message
            }), 500
            
    except Exception as e:
        logging.error(f"Ошибка при отправке кода смены email: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Ошибка: {str(e)}'
        }), 500

@app.route('/verify-email-change', methods=['POST'])
@login_required
def verify_email_change():
    """Подтверждает смену email по коду и автоматически меняет на новый"""
    try:
        data = request.json
        code = data.get('code', '')
        
        cleanup_expired_email_codes()
        
        key = f"email_change_{current_user.id}"
        if key not in temp_codes:
            return jsonify({
                'success': False,
                'message': 'Код не найден. Запросите новый код.'
            }), 400
        
        code_data = temp_codes[key]
        
        # ПРОВЕРЯЕМ, ЧТО НОВЫЙ EMAIL ЕСТЬ В ДАННЫХ
        new_email = code_data.get('new_email')
        if not new_email:
            return jsonify({
                'success': False,
                'message': 'Ошибка: новый email не найден в данных'
            }), 400
        
        if code != code_data['code']:
            return jsonify({
                'success': False,
                'message': 'Неверный код'
            }), 400
        
        # Проверяем, что новый email всё ещё свободен
        existing_user = User.query.filter_by(email=new_email).first()
        if existing_user and existing_user.id != current_user.id:
            return jsonify({
                'success': False,
                'message': 'Этот email уже занят другим пользователем'
            }), 400
        
        # Сохраняем старый email для уведомления
        old_email = current_user.email
        
        # Обновляем email - УБЕДИМСЯ, ЧТО NEW_EMAIL НЕ ПУСТОЙ
        if not new_email:
            return jsonify({
                'success': False,
                'message': 'Ошибка: новый email пустой'
            }), 400
            
        current_user.email = new_email
        db.session.commit()
        
        # Удаляем использованный код
        del temp_codes[key]
        
        # Отправляем уведомление на старый email о смене
        try:
            send_email_change_notification(old_email, current_user.username, new_email)
        except:
            pass  # Если не отправится - не критично
        
        return jsonify({
            'success': True,
            'message': 'Email успешно изменен!'
        })
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Ошибка при смене email: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Ошибка: {str(e)}'
        }), 500

def send_email_change_confirmation_email(recipient_email, reset_code, username, new_email):
    """Отправляет код подтверждения на СТАРЫЙ email"""
    try:
        smtp_server = app.config['MAIL_SERVER']
        smtp_port = app.config['MAIL_PORT']
        sender_email = app.config['MAIL_USERNAME']
        sender_password = app.config['MAIL_PASSWORD']
        
        message = MIMEMultipart('alternative')
        message['Subject'] = '⚠️ Подтверждение смены email - Psychoolds'
        message['From'] = sender_email
        message['To'] = recipient_email
        
        text = f"""
        Здравствуйте, {username}!
        
        Вы запросили смену email в социальной сети Psychoolds на: {new_email}
        
        Для подтверждения введите код: {reset_code}
        
        Если это были НЕ ВЫ, немедленно смените пароль и свяжитесь с поддержкой!
        
        С уважением,
        Команда Psychoolds
        """
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #dc3545, #bb2d3b); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px; }}
                .code {{ background: white; font-size: 32px; font-weight: bold; color: #dc3545; padding: 15px; text-align: center; border-radius: 10px; margin: 20px 0; letter-spacing: 5px; }}
                .warning {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #dc3545; }}
                .new-email {{ background: #e7f3ff; padding: 10px; border-radius: 5px; margin: 10px 0; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #6c757d; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🔐 Подтверждение смены email</h2>
                </div>
                <div class="content">
                    <p>Здравствуйте, <strong>{username}</strong>!</p>
                    
                    <div class="new-email">
                        <strong>Новый email:</strong> {new_email}
                    </div>
                    
                    <p>Для подтверждения смены email введите код:</p>
                    
                    <div class="code">
                        {reset_code}
                    </div>
                    
                    <div class="warning">
                        <strong>⚠️ ВНИМАНИЕ!</strong> Если вы не запрашивали смену email, 
                        немедленно смените пароль и свяжитесь с поддержкой!
                    </div>
                    
                    <p>Код действителен в течение 15 минут.</p>
                </div>
                <div class="footer">
                    <p>С уважением,<br>Команда Psychoolds</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message.attach(MIMEText(text, 'plain'))
        message.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)
            
        return True, "Код отправлен на ваш текущий email"
        
    except Exception as e:
        logging.error(f"Ошибка отправки email подтверждения: {str(e)}")
        return False, f"Ошибка отправки: {str(e)}"

def send_email_change_notification(old_email, username, new_email):
    """Отправляет уведомление на старый email об успешной смене"""
    try:
        smtp_server = app.config['MAIL_SERVER']
        smtp_port = app.config['MAIL_PORT']
        sender_email = app.config['MAIL_USERNAME']
        sender_password = app.config['MAIL_PASSWORD']
        
        message = MIMEMultipart('alternative')
        message['Subject'] = '✅ Email изменен - Psychoolds'
        message['From'] = sender_email
        message['To'] = old_email
        
        text = f"""
        Здравствуйте, {username}!
        
        Ваш email в социальной сети Psychoolds был успешно изменен на: {new_email}
        
        Если это были НЕ ВЫ, немедленно свяжитесь с поддержкой!
        
        С уважением,
        Команда Psychoolds
        """
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #28a745, #218838); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 10px 10px; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #28a745; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #6c757d; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>✅ Email успешно изменен</h2>
                </div>
                <div class="content">
                    <p>Здравствуйте, <strong>{username}</strong>!</p>
                    
                    <div class="success">
                        <strong>Новый email:</strong> {new_email}
                    </div>
                    
                    <p>Ваш email в социальной сети Psychoolds был успешно изменен.</p>
                    
                    <p><small>Если вы не совершали это действие, немедленно свяжитесь с поддержкой!</small></p>
                </div>
                <div class="footer">
                    <p>С уважением,<br>Команда Psychoolds</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message.attach(MIMEText(text, 'plain'))
        message.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)
            
        return True
        
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления: {str(e)}")
        return False

def cleanup_expired_email_codes():
    """Очищает просроченные коды для смены email"""
    current_time = time.time()
    expired = [key for key, data in temp_codes.items() 
               if key.startswith('email_') and current_time - data['timestamp'] > 900]
    for key in expired:
        del temp_codes[key]

@app.route('/chat', methods=['POST'])
@login_required
def chat_with_ai():
    """Обработка сообщений для AI через OpenRouter (упрощенная версия)"""
    if not AI_ENABLED or not OPENROUTER_API_KEY:
        return jsonify({"error": "AI не настроен или недоступен"}), 503
    
    try:
        data = request.json
        user_message = data.get('message', '')
        
        if not user_message:
            return jsonify({"error": "Пустое сообщение"}), 400
        
        logging.info(f"AI запрос от {current_user.username}: {user_message[:50]}...")
        
        # Формируем запрос к OpenRouter
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5000",
            "X-Title": "Social Network AI"
        }
        
        payload = {
            "model": AI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": f"""Ты полезный ассистент в социальной сети. 
Пользователь: {current_user.username}
Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}

Отвечай дружелюбно, на русском языке. 
Если просят написать код - пиши с пояснениями.
Если не знаешь ответа - честно скажи об этом.
Отвечай подробно и развернуто."""
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
            "stream": False  # Отключаем стриминг для начала
        }
        
        # Отправляем запрос к OpenRouter
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                ai_response = result['choices'][0]['message']['content']
                return jsonify({"response": ai_response})
            else:
                return jsonify({"error": "Неожиданный формат ответа"}), 500
        else:
            error_msg = f"OpenRouter error: {response.status_code}"
            logging.error(error_msg)
            return jsonify({"error": error_msg}), response.status_code
        
    except Exception as e:
        logging.error(f"AI Error: {str(e)}")
        return jsonify({"error": f"Ошибка AI: {str(e)}"}), 500

@app.route('/ai-status', methods=['GET'])
@login_required
def ai_status():
    """Проверка статуса AI"""
    return jsonify({
        'enabled': AI_ENABLED,
        'model': AI_MODEL,
        'timestamp': datetime.now().isoformat()
    })


# ========== ЗАПУСК ==========

if __name__ == '__main__':

    with app.app_context():
        cleanup_expired_codes()
        cleanup_expired_reset_codes()
        print("✅ Просроченные коды очищены")

    app.run(debug=True, host='0.0.0.0', port=5000)