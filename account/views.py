from django.shortcuts import render, redirect
from .forms import CreateUserForm, LoginForm,UserUpdateForm
from django.contrib.sites.shortcuts import get_current_site
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes,force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.contrib.auth.models import User
from django.contrib.auth.models import auth
from django.contrib.auth import authenticate, login,logout
from django.contrib.auth.decorators import login_required



from django.core.mail import send_mail


from .token import user_tokenizer_generate



# Create your views here.

def register(request):
    form = CreateUserForm()

    if request.method=='POST':
        form=CreateUserForm(request.POST)
        if form.is_valid():
            user=form.save()

            user.is_active=False
            user.save()

            #Email Verification

            current_site=get_current_site(request)
            subject='Account Verification Email'
            message=render_to_string('accounts/registration/email-verification.html', {
                'user': user,
                'domain': current_site,
                'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': user_tokenizer_generate.make_token(user),

            })

            user.email_user(subject=subject, message=message)



            return redirect('email-verification-sent')
    
    context={'form':form}
    

    return render (request, 'accounts/registration/register.html',context)


def email_verification(request, uidb64,token):
    unique_id= force_str(urlsafe_base64_decode(uidb64))
    user=User.objects.get(pk=unique_id)

    #Success

    if user and user_tokenizer_generate.check_token(user, token):

        user.is_active=True

        user.save()

        return redirect ('email-verification-success')
    
    #failed
    else:

        return redirect ('email-verification-failed')

    


def email_verification_sent(request):
    return render(request, 'accounts/registration/email-verification-sent.html')

def email_verification_success(request):
    return render(request, 'accounts/registration/email-verification-success.html')

def email_verification_failed(request):
    return render(request, 'accounts/registration/email-verification-failed.html')






def my_login(request):
    form=LoginForm
    if request.method=='POST':
        form=LoginForm(request,data=request.POST)
        if form.is_valid():
            username=request.POST.get('username')
            password=request.POST.get('password')

            user=authenticate(username=username, password=password)
            if user is not None:
                auth.login(request, user)

                return redirect('dashboard')
    context={'form': form}
    return render(request, 'accounts/my-login.html', context)


@login_required(login_url='my-login')
def dashboard(request):
    return render(request, 'accounts/dashboard.html',{})



def user_logout(request):
    auth.logout(request)

    return redirect('store')


@login_required(login_url='my-login')
def profile_management(request):

    user_form=UserUpdateForm(instance=request.user)

    if request.method=='POST':


        user_form=UserUpdateForm(request.POST, instance=request.user)

        if user_form.is_valid():

            user_form.save()

            return redirect('dashboard')
    
    

    context={'user_form':user_form}
    




    return render(request, 'accounts/profile-management.html',context)


@login_required(login_url='my-login')
def delete_account(request):

    user=User.objects.get(id=request.user.id)

    if request.method=='POST':

        user.delete()
        return redirect('store')
    

    return render(request, 'accounts/delete-account.html',{})



